# benchmarks/eval_v2.py
"""
Scorer V2 orchestrator.

Runs Tier 1 (deterministic) locally, builds Tier 2 (taxonomy) prompt
for Sonnet classification, combines into final score.

Usage:
    # Tier 1 only (instant, no LLM)
    python -m benchmarks.eval_v2 deterministic \
        --plan benchmarks/results/.../plan_01.json \
        --structural-index benchmarks/ideal_context.json

    # Prepare taxonomy prompt for Sonnet
    python -m benchmarks.eval_v2 taxonomy-prompt \
        --plan benchmarks/results/.../plan_01.json \
        --structural-index benchmarks/ideal_context.json \
        --taxonomy benchmarks/streaming_taxonomy.json

    # Full batch (Tier 1 for all plans + taxonomy prompts)
    python -m benchmarks.eval_v2 batch \
        --results-dir benchmarks/results/.../  \
        --context-file benchmarks/ideal_context.json \
        --taxonomy benchmarks/streaming_taxonomy.json
"""

import json
import logging
import re
import statistics
import sys
from pathlib import Path

import typer

from .eval_v2_deterministic import run_deterministic_checks
from .eval_v2_schemas import (
    BatchScoreV2,
    PlanScoreV2,
)
from .eval_v2_taxonomy import (
    build_taxonomy_prompt,
    load_taxonomy,
    parse_taxonomy_response,
)

logger = logging.getLogger(__name__)
app = typer.Typer(help="Scorer V2 — deterministic + taxonomy evaluation")

# Match only plan_NN.json (not plan_01_trimmed.json or plan_01.v2_score.json)
_PLAN_RE = re.compile(r"^plan_\d+\.json$")


def _find_plan_files(plan_dir: Path) -> list[Path]:
    """Find canonical plan files (plan_NN.json only)."""
    return sorted(p for p in plan_dir.glob("plan_*.json") if _PLAN_RE.match(p.name))


# ---------------------------------------------------------------------------
# Single-plan scoring
# ---------------------------------------------------------------------------


def score_plan_deterministic(
    plan_path: Path,
    structural_index: str,
    query: str = "",
    taxonomy_files: dict[str, str] | None = None,
    source_dir: str = "",
) -> PlanScoreV2:
    """Run Tier 1 deterministic checks on a single plan. Zero LLM cost."""
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))

    det_report = run_deterministic_checks(
        plan_data,
        structural_index,
        task_requires_streaming=True,
        taxonomy_files=taxonomy_files,
        source_dir=source_dir,
    )

    return PlanScoreV2(
        plan_file=plan_path.name,
        query=query,
        deterministic=det_report,
        taxonomy=None,
        deterministic_score=det_report.deterministic_score,
        taxonomy_score=None,
        final_score=det_report.deterministic_score,  # No taxonomy yet
    )


def score_plan_full(
    plan_path: Path,
    structural_index: str,
    taxonomy_response: str,
    taxonomy_path: Path,
    query: str = "",
) -> PlanScoreV2:
    """Run Tier 1 + parse Tier 2 classification for a single plan."""
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    taxonomy_def = load_taxonomy(taxonomy_path)

    det_report = run_deterministic_checks(
        plan_data,
        structural_index,
        task_requires_streaming=True,
        taxonomy_files=taxonomy_def.required_files or None,
    )

    tax_report = parse_taxonomy_response(taxonomy_response, taxonomy_def)

    final = round(
        det_report.deterministic_score * 0.6 + tax_report.taxonomy_score * 0.4,
        1,
    )

    return PlanScoreV2(
        plan_file=plan_path.name,
        query=query,
        deterministic=det_report,
        taxonomy=tax_report,
        deterministic_score=det_report.deterministic_score,
        taxonomy_score=tax_report.taxonomy_score,
        final_score=final,
    )


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------


def score_batch_deterministic(
    plan_dir: Path,
    structural_index: str,
    query: str = "",
    taxonomy_files: dict[str, str] | None = None,
    source_dir: str = "",
) -> BatchScoreV2:
    """Run Tier 1 on all plans in a directory."""
    plan_files = _find_plan_files(plan_dir)
    if not plan_files:
        raise FileNotFoundError(f"No plan_*.json files in {plan_dir}")

    scores = []
    for pf in plan_files:
        score = score_plan_deterministic(pf, structural_index, query, taxonomy_files, source_dir)
        scores.append(score)

    det_scores = [s.deterministic_score for s in scores]
    final_scores = [s.final_score for s in scores]

    return BatchScoreV2(
        query=query,
        plans_scored=len(scores),
        deterministic_average=round(statistics.mean(det_scores), 1),
        deterministic_min=min(det_scores),
        deterministic_max=max(det_scores),
        taxonomy_average=None,
        final_average=round(statistics.mean(final_scores), 1),
        final_min=min(final_scores),
        final_max=max(final_scores),
        scores=scores,
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_deterministic_report(score: PlanScoreV2) -> str:
    """Format a single plan's deterministic results as readable markdown."""
    det = score.deterministic
    lines = [f"# Scorer V2 — {score.plan_file}\n"]

    # Overall
    lines.append(f"**Deterministic Score: {det.deterministic_score}/100**")
    lines.append(f"- Completeness: {det.completeness_score}/30")
    lines.append(f"- Artifact Quality: {det.artifact_quality_score}/50")
    lines.append(f"- Consistency: {det.consistency_score}/20")

    # Completeness detail
    comp = det.completeness
    lines.append(f"\n## Completeness ({comp.required_ratio:.0%} required files)")
    if comp.missing_required:
        lines.append("**Missing required:**")
        for f in comp.missing_required:
            lines.append(f"  - {f}")
    if comp.present_required:
        lines.append("Present required: " + ", ".join(comp.present_required))
    if comp.present_recommended:
        lines.append("Present recommended: " + ", ".join(comp.present_recommended))

    # Per-artifact
    lines.append("\n## Per-Artifact Checks")
    lines.append(
        "| File | Score | Parseable | Fab Self | Fab Chain | Fab Field | Fab Class | Yield | RetType | NotImpl | stdout |"
    )
    lines.append(
        "|------|-------|-----------|----------|-----------|-----------|-----------|-------|---------|---------|--------|"
    )
    for ac in det.artifact_checks:
        yield_str = str(ac.has_yield) if ac.has_yield is not None else "-"
        ret_str = str(ac.has_correct_return_type) if ac.has_correct_return_type is not None else "-"
        lines.append(
            f"| {ac.filename} | {ac.score:.0f} | {ac.parseable} | "
            f"{ac.fabricated_self_methods} | {ac.fabricated_chained_methods} | "
            f"{ac.fabricated_field_access} | {ac.fabricated_classes} | "
            f"{yield_str} | {ret_str} | {ac.has_not_implemented} | {ac.has_sys_stdout} |"
        )

    # Consistency
    lines.append("\n## Cross-Artifact Consistency")
    for cc in det.consistency_checks:
        status = "PASS" if cc.passed else "FAIL"
        lines.append(f"- [{status}] **{cc.check}**: {cc.detail}")

    return "\n".join(lines)


def format_batch_report(batch: BatchScoreV2) -> str:
    """Format batch results as markdown summary."""
    lines = [f"# Scorer V2 Batch ({batch.plans_scored} plans)\n"]

    lines.append("## Summary")
    lines.append(
        f"- Deterministic avg: {batch.deterministic_average}/100 "
        f"(range: {batch.deterministic_min}-{batch.deterministic_max})"
    )
    if batch.taxonomy_average is not None:
        lines.append(f"- Taxonomy avg: {batch.taxonomy_average}/100")
    lines.append(
        f"- Final avg: {batch.final_average}/100 (range: {batch.final_min}-{batch.final_max})"
    )

    # Per-plan table
    lines.append("\n## Per-Plan Scores")
    lines.append("| Plan | Deterministic | Completeness | Artifact Qual | Consistency | Final |")
    lines.append("|------|---------------|--------------|---------------|-------------|-------|")
    for s in batch.scores:
        det = s.deterministic
        lines.append(
            f"| {s.plan_file} | {det.deterministic_score} | "
            f"{det.completeness_score}/30 | {det.artifact_quality_score}/50 | "
            f"{det.consistency_score}/20 | {s.final_score} |"
        )

    # Common issues across plans
    lines.append("\n## Common Issues")
    all_missing = []
    total_fab = 0
    total_parse_fail = 0
    for s in batch.scores:
        all_missing.extend(s.deterministic.completeness.missing_required)
        for ac in s.deterministic.artifact_checks:
            total_fab += (
                ac.fabricated_self_methods
                + ac.fabricated_chained_methods
                + ac.fabricated_field_access
            )
            if not ac.parseable:
                total_parse_fail += 1

    if all_missing:
        from collections import Counter

        missing_counts = Counter(all_missing)
        lines.append("**Frequently missing required files:**")
        for f, count in missing_counts.most_common(5):
            lines.append(f"  - {f} (missing in {count}/{batch.plans_scored} plans)")

    lines.append(f"- Total fabrication violations: {total_fab}")
    lines.append(f"- Parse failures: {total_parse_fail}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command("deterministic")
def cmd_deterministic(
    plan: str = typer.Option(..., help="Path to plan_XX.json"),
    context_file: str = typer.Option(..., help="ideal_context.json for structural index"),
    source_dir: str = typer.Option("", help="Target codebase dir (for full class validation)"),
    taxonomy: str = typer.Option(
        "benchmarks/streaming_taxonomy.json",
        help="Taxonomy definition JSON (for required files)",
    ),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Task query",
    ),
):
    """Run Tier 1 deterministic checks on a single plan."""
    context = json.loads(Path(context_file).read_text())
    structural_index = context.get("synthesized", "")

    tax_files = None
    tax_path = Path(taxonomy)
    if tax_path.exists():
        tax_def = load_taxonomy(tax_path)
        tax_files = tax_def.required_files or None

    score = score_plan_deterministic(Path(plan), structural_index, query, tax_files, source_dir)
    report = format_deterministic_report(score)
    print(report, file=sys.stderr)

    # Also write JSON
    out_path = Path(plan).with_suffix(".v2_score.json")
    out_path.write_text(
        json.dumps(score.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}", file=sys.stderr)


@app.command("taxonomy-prompt")
def cmd_taxonomy_prompt(
    plan: str = typer.Option(..., help="Path to plan_XX.json"),
    context_file: str = typer.Option(..., help="ideal_context.json for structural index"),
    taxonomy: str = typer.Option(
        "benchmarks/streaming_taxonomy.json",
        help="Taxonomy definition JSON",
    ),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Task query",
    ),
):
    """Generate a taxonomy classification prompt for Sonnet."""
    context = json.loads(Path(context_file).read_text())
    structural_index = context.get("synthesized", "")
    plan_data = json.loads(Path(plan).read_text(encoding="utf-8"))
    plan_json = json.dumps(plan_data, indent=2, default=str)

    taxonomy_def = load_taxonomy(Path(taxonomy))
    det_report = run_deterministic_checks(plan_data, structural_index, task_requires_streaming=True)

    prompt = build_taxonomy_prompt(plan_json, det_report, taxonomy_def, structural_index)

    # Write to file
    out_path = Path(plan).with_name(Path(plan).stem.replace("plan_", "score_v2_prompt_") + ".md")
    out_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote taxonomy prompt: {out_path} ({len(prompt)} chars)", file=sys.stderr)


@app.command("batch")
def cmd_batch(
    results_dir: str = typer.Option(..., help="Directory with plan_*.json files"),
    context_file: str = typer.Option(..., help="ideal_context.json"),
    source_dir: str = typer.Option("", help="Target codebase dir (for full class validation)"),
    taxonomy: str = typer.Option(
        "benchmarks/streaming_taxonomy.json",
        help="Taxonomy definition JSON",
    ),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Task query",
    ),
):
    """Run Tier 1 on all plans + generate Tier 2 prompts."""
    context = json.loads(Path(context_file).read_text())
    structural_index = context.get("synthesized", "")
    plan_dir = Path(results_dir)
    taxonomy_def = load_taxonomy(Path(taxonomy))
    tax_files = taxonomy_def.required_files or None

    # Run deterministic scoring
    batch = score_batch_deterministic(plan_dir, structural_index, query, tax_files, source_dir)

    # Generate taxonomy prompts for each plan
    for pf in _find_plan_files(plan_dir):
        plan_data = json.loads(pf.read_text(encoding="utf-8"))
        plan_json = json.dumps(plan_data, indent=2, default=str)

        det_report = run_deterministic_checks(
            plan_data,
            structural_index,
            task_requires_streaming=True,
            taxonomy_files=tax_files,
            source_dir=source_dir,
        )

        prompt = build_taxonomy_prompt(plan_json, det_report, taxonomy_def, structural_index)

        num = pf.stem.replace("plan_", "")
        prompt_path = plan_dir / f"score_v2_prompt_{num}.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        logger.info(f"Wrote {prompt_path.name} ({len(prompt)} chars)")

    # Write batch report
    report = format_batch_report(batch)
    report_path = plan_dir / "SCORE_V2_SUMMARY.md"
    report_path.write_text(report, encoding="utf-8")

    # Write JSON
    json_path = plan_dir / "scores_v2.json"
    json_path.write_text(
        json.dumps(batch.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    print(report, file=sys.stderr)
    print(f"\nWrote {report_path} and {json_path}", file=sys.stderr)


if __name__ == "__main__":
    app()
