# fitz_forge/planning/quality/indicators.py
"""Compute runtime quality indicators on a finalized plan.

Four deterministic dimensions — Coverage, Craft, Groundedness,
Actionability — that can be computed locally without a task taxonomy,
a Sonnet grader, or any other external dependency. Surfaced to the
user on ``fitz get <id>`` so the plan comes with honest quality
signals even though the offline benchmark-scoring framework doesn't
run in production.

Consistent with ``benchmarks/eval_v2_deterministic.py`` where
possible — the benchmark scorer uses the same underlying checks with
a taxonomy's curated ``required_files`` list. Here we substitute the
plan's own ``context.needed_artifacts`` as the intent baseline. A
file in ``needed_artifacts`` that ships as a ``NotImplementedError``
stub is a self-miss even without an external rubric to compare
against.

Codebase-/language-agnostic. A non-Python artifact that gets flagged
by the artifact-level Python checks is counted as clean (checks
simply don't fire); the Groundedness pass still runs the full
structural index against every artifact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_PLACEHOLDER_VERIFICATION_RE = re.compile(
    r"^\s*(todo|tbd|write tests|run tests|verify manually|manual verification)\s*$",
    re.IGNORECASE,
)


@dataclass
class QualityIndicators:
    """Four deterministic quality dimensions for a finalized plan."""

    coverage: float
    craft: float
    groundedness: float
    actionability: float

    coverage_detail: dict[str, Any] = field(default_factory=dict)
    craft_detail: dict[str, Any] = field(default_factory=dict)
    groundedness_detail: dict[str, Any] = field(default_factory=dict)
    actionability_detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_needed_entry(entry: str) -> str:
    """Extract just the filename from a needed_artifacts entry.

    Accepts ``"path/to/file.py -- purpose"`` or bare ``"path/to/file.py"``.
    """
    if not isinstance(entry, str):
        return ""
    stripped = entry.strip()
    if not stripped:
        return ""
    if " -- " in stripped:
        return stripped.split(" -- ", 1)[0].strip()
    return stripped


def _artifact_filename_set(artifacts: list[Any]) -> set[str]:
    out: set[str] = set()
    for a in artifacts or []:
        if isinstance(a, dict):
            fn = a.get("filename")
        else:
            fn = getattr(a, "filename", None)
        if isinstance(fn, str) and fn.strip():
            out.add(fn.strip())
    return out


def _compute_coverage(plan_data: dict, artifacts: list[dict]) -> tuple[float, dict]:
    """% of declared ``needed_artifacts`` that actually shipped AND are not stubs.

    With no declared intent (``needed_artifacts`` empty), coverage is
    ``N/A`` — reported as 100 with a detail note so the user knows the
    check was vacuous.
    """
    needed = plan_data.get("context", {}).get("needed_artifacts") or []
    declared = []
    seen: set[str] = set()
    for entry in needed:
        fn = _parse_needed_entry(entry)
        if fn and fn not in seen:
            declared.append(fn)
            seen.add(fn)

    if not declared:
        return 100.0, {
            "declared": 0,
            "shipped": 0,
            "stubbed": [],
            "missing": [],
            "note": "No declared needed_artifacts — coverage vacuously 100.",
        }

    produced_by_path = _artifact_filename_set(artifacts)
    content_by_name: dict[str, str] = {}
    for a in artifacts or []:
        if isinstance(a, dict):
            fn = (a.get("filename") or "").strip()
            if fn:
                content_by_name[fn] = a.get("content", "") or ""

    shipped: list[str] = []
    stubbed: list[str] = []
    missing: list[str] = []
    for fn in declared:
        content = None
        for path, body in content_by_name.items():
            if path == fn or path.endswith("/" + fn) or fn.endswith("/" + path):
                content = body
                break
        if content is None and fn in produced_by_path:
            content = content_by_name.get(fn, "")
        if content is None:
            missing.append(fn)
            continue
        # Stub detection: a file whose body is dominated by NotImplementedError
        # (with or without explanatory comments) delivers no real implementation.
        if "NotImplementedError" in content and "raise NotImplementedError" in content:
            stubbed.append(fn)
            continue
        shipped.append(fn)

    real_count = len(shipped)
    coverage = round(real_count / len(declared) * 100, 1)
    return coverage, {
        "declared": len(declared),
        "shipped": real_count,
        "stubbed": stubbed,
        "missing": missing,
    }


def _compute_craft(plan_data: dict, structural_index: str, source_dir: str) -> tuple[float, dict]:
    """Per-artifact AST checks + consistency on what was produced.

    Reuses ``check_all_artifacts_v2`` and ``check_cross_artifact_consistency``
    from the benchmark scorer, so the score is identical to what the
    offline evaluator would compute on this plan.
    """
    artifacts = plan_data.get("design", {}).get("artifacts") or []
    if not artifacts:
        return 0.0, {"note": "No artifacts to score."}

    from fitz_forge.planning.validation.scoring import (
        check_all_artifacts_v2,
        check_cross_artifact_consistency,
    )

    artifact_dicts = [
        {"filename": a.get("filename", ""), "content": a.get("content", "")}
        for a in artifacts
        if isinstance(a, dict)
    ]
    try:
        ac_checks = check_all_artifacts_v2(
            artifact_dicts, structural_index, True, source_dir
        )
        cc_checks = check_cross_artifact_consistency(
            artifact_dicts, ac_checks, structural_index, source_dir
        )
    except Exception as e:
        logger.warning(f"Craft computation failed ({e}); defaulting to 0")
        return 0.0, {"note": f"Error: {e}"}

    if ac_checks:
        weights = [max(10, a.content_lines) for a in ac_checks]
        total_weight = sum(weights)
        artifact_mean = (
            sum(a.score * w for a, w in zip(ac_checks, weights)) / total_weight
        )
    else:
        artifact_mean = 0.0

    if cc_checks:
        consistency_ratio = sum(1 for c in cc_checks if c.passed) / len(cc_checks)
    else:
        consistency_ratio = 1.0

    craft = round((artifact_mean + consistency_ratio * 100) / 2, 1)
    fabrications = sum(
        a.fabricated_self_methods
        + a.fabricated_chained_methods
        + a.fabricated_field_access
        + a.fabricated_classes
        for a in ac_checks
    )
    parse_failures = sum(1 for a in ac_checks if not a.parseable)
    return craft, {
        "artifacts": len(ac_checks),
        "artifact_quality": round(artifact_mean, 1),
        "consistency": round(consistency_ratio * 100, 1),
        "fabrications": fabrications,
        "parse_failures": parse_failures,
    }


def _compute_groundedness(
    plan_data: dict, structural_index: str, source_dir: str
) -> tuple[float, dict]:
    """% of artifacts free of grounding violations against the real codebase."""
    artifacts = plan_data.get("design", {}).get("artifacts") or []
    if not artifacts:
        return 100.0, {"note": "No artifacts to ground."}

    artifact_dicts = [
        {"filename": a.get("filename", ""), "content": a.get("content", "")}
        for a in artifacts
        if isinstance(a, dict)
    ]

    try:
        from fitz_forge.planning.validation.grounding.check import check_all_artifacts

        violations = check_all_artifacts(
            artifact_dicts, structural_index, source_dir=source_dir
        )
    except Exception as e:
        logger.warning(f"Groundedness computation failed ({e}); defaulting to 100")
        return 100.0, {"note": f"Error: {e}"}

    dirty = {v.artifact for v in violations}
    clean = len(artifact_dicts) - len(dirty)
    score = round(clean / len(artifact_dicts) * 100, 1)
    sample = [
        {"artifact": v.artifact, "symbol": v.symbol, "kind": v.kind}
        for v in violations[:5]
    ]
    return score, {
        "artifacts": len(artifact_dicts),
        "violations": len(violations),
        "artifacts_with_violations": len(dirty),
        "examples": sample,
    }


def _compute_actionability(plan_data: dict) -> tuple[float, dict]:
    """% of roadmap phases that carry a concrete verification command."""
    phases = plan_data.get("roadmap", {}).get("phases") or []
    if not phases:
        return 0.0, {"note": "Plan has no roadmap phases."}

    actionable = 0
    missing: list[str] = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        cmd = str(phase.get("verification_command") or "").strip()
        name = phase.get("name") or f"phase {phase.get('number', '?')}"
        if not cmd or _PLACEHOLDER_VERIFICATION_RE.match(cmd):
            missing.append(name)
            continue
        actionable += 1
    score = round(actionable / len(phases) * 100, 1)
    return score, {
        "phases": len(phases),
        "actionable": actionable,
        "missing_verification": missing,
    }


def compute_quality_indicators(
    plan_data: dict,
    structural_index: str = "",
    source_dir: str = "",
) -> QualityIndicators:
    """Compute all four indicators from a finalized plan.

    ``plan_data`` is the ``prior_outputs`` / ``PlanOutput.model_dump()``
    shape — has ``context``, ``design``, ``roadmap`` sections.
    ``structural_index`` and ``source_dir`` are passed to the grounding
    pass; if both are empty, groundedness falls back to "no anchor to
    check against" and reports 100 with a note.
    """
    coverage, cov_d = _compute_coverage(plan_data, plan_data.get("design", {}).get("artifacts") or [])
    craft, craft_d = _compute_craft(plan_data, structural_index, source_dir)
    groundedness, ground_d = _compute_groundedness(plan_data, structural_index, source_dir)
    actionability, act_d = _compute_actionability(plan_data)

    return QualityIndicators(
        coverage=coverage,
        craft=craft,
        groundedness=groundedness,
        actionability=actionability,
        coverage_detail=cov_d,
        craft_detail=craft_d,
        groundedness_detail=ground_d,
        actionability_detail=act_d,
    )


def format_indicators_markdown(indicators: QualityIndicators) -> str:
    """Render as a ``## Quality Indicators`` markdown section.

    Used by the plan renderer so the block lands at the end of the
    plan file and ``fitz get`` naturally shows it.
    """
    lines = [
        "## Quality Indicators",
        "",
        "_Deterministic, local-only checks — no API calls. Run at plan finalization to give you honest signals alongside the plan. See `docs/SCORER-V2-SPEC.md` for definitions._",
        "",
        "| Indicator | Score | Detail |",
        "|---|---:|---|",
    ]

    cov = indicators.coverage_detail
    if cov.get("note"):
        cov_line = cov["note"]
    else:
        bits: list[str] = []
        bits.append(
            f"{cov.get('shipped', 0)}/{cov.get('declared', 0)} declared artifacts shipped with real code"
        )
        if cov.get("stubbed"):
            bits.append(f"stubbed: {', '.join(cov['stubbed'])}")
        if cov.get("missing"):
            bits.append(f"missing: {', '.join(cov['missing'])}")
        cov_line = "; ".join(bits)
    lines.append(f"| **Coverage** | {indicators.coverage:.1f} | {cov_line} |")

    craft = indicators.craft_detail
    if craft.get("note"):
        craft_line = craft["note"]
    else:
        craft_line = (
            f"{craft.get('fabrications', 0)} fabrications, "
            f"{craft.get('parse_failures', 0)} parse errors across "
            f"{craft.get('artifacts', 0)} artifacts"
        )
    lines.append(f"| **Craft** | {indicators.craft:.1f} | {craft_line} |")

    ground = indicators.groundedness_detail
    if ground.get("note"):
        ground_line = ground["note"]
    else:
        ground_line = (
            f"{ground.get('violations', 0)} unresolved reference(s) across "
            f"{ground.get('artifacts', 0)} artifacts"
        )
    lines.append(f"| **Groundedness** | {indicators.groundedness:.1f} | {ground_line} |")

    act = indicators.actionability_detail
    if act.get("note"):
        act_line = act["note"]
    else:
        act_line = (
            f"{act.get('actionable', 0)}/{act.get('phases', 0)} roadmap phases "
            f"carry a verification command"
        )
    lines.append(f"| **Actionability** | {indicators.actionability:.1f} | {act_line} |")

    lines.append("")
    lines.append(
        "_These are self-consistency and grounding checks (`did the plan deliver what it said it would`, `do the symbols exist in your codebase`), not an oracle judgment of plan quality. Architectural-correctness grading needs a Sonnet pass and happens offline in the benchmark harness — not here._"
    )
    return "\n".join(lines)
