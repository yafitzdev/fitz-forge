# fitz_forge/benchmarks/score_taxonomy.py
"""Tier-2 taxonomy scoring via Claude Code headless (`claude -p`).

Runs after Tier-1 deterministic. For each plan the prompt file written by
``_prepare_scoring_v2`` is fed to Sonnet, which returns a JSON classification
(architecture + per-file taxonomy ids). Classifications are mapped to scores
via the taxonomy's own score field, aggregated, and persisted alongside
Tier-1 outputs so both tiers live in the same ``scores_v2.json``.

No Anthropic SDK here — we shell out to the ``claude`` CLI. Authentication
piggybacks on the user's Claude Code login.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_TIMEOUT_S = 180
DEFAULT_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Headless Claude Code invocation
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of a Sonnet response. Tolerates fences + preamble."""
    raw = raw.strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    # If there's preamble, find the first {...} balanced blob.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in output: {raw[:200]!r}")
    return json.loads(raw[start : end + 1])


async def _invoke_claude(prompt: str, model: str, timeout: int) -> str:
    """Run ``claude -p --model <model>`` with prompt on stdin.

    Prompt is piped via stdin rather than passed as an argv arg — the
    taxonomy scoring prompts run ~50 KB, well past the Windows command-
    line length limit (~8 KB argv → WinError 206). Stdin has no such cap.
    """
    proc = await asyncio.create_subprocess_exec(
        "claude",
        "-p",
        "--model",
        model,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"claude -p timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {proc.returncode}: {stderr.decode('utf-8', 'replace')[:400]}"
        )
    return stdout.decode("utf-8", "replace")


async def _score_one_plan(
    prompt_path: Path,
    model: str,
    timeout: int,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    """Score a single plan's Tier-2 prompt. Non-fatal on error — records the error."""
    async with sem:
        plan_num = prompt_path.stem.replace("score_v2_prompt_", "")
        prompt = prompt_path.read_text(encoding="utf-8")
        # Append a minimal instruction to force JSON-only output. The prompts
        # already say "Respond with ONLY a JSON object" but Sonnet sometimes
        # prefixes with reasoning. The fence/brace extractor handles that.
        prompt = prompt + "\n\nReturn ONLY the JSON object. No markdown, no explanation."
        try:
            raw = await _invoke_claude(prompt, model, timeout)
            classification = _extract_json(raw)
            logger.info(f"tier2: plan_{plan_num} classified")
            return {"plan": plan_num, "ok": True, "classification": classification}
        except Exception as e:
            logger.warning(f"tier2: plan_{plan_num} failed — {e}")
            return {"plan": plan_num, "ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Classification normalisation
# ---------------------------------------------------------------------------


def _pluck_arch_id(cls: dict[str, Any]) -> str | None:
    """Extract the architecture taxonomy id from whatever shape Sonnet returned."""
    arch = cls.get("architecture") or cls.get("overall_architecture")
    if isinstance(arch, str):
        return arch
    if isinstance(arch, dict):
        return arch.get("id") or arch.get("taxonomy_id") or arch.get("classification")
    return None


def _pluck_file_ids(cls: dict[str, Any]) -> dict[str, str]:
    """Return {filename: tax_id} from whatever shape Sonnet returned.

    Sonnet's shape varies — list of {filename, id}, dict of {path: id}, or
    dict of {path: {id: ...}}. Normalise to flat {path: id}.
    """
    out: dict[str, str] = {}
    arts = cls.get("artifacts") or cls.get("per_file") or cls.get("classification")
    if isinstance(arts, list):
        for item in arts:
            if not isinstance(item, dict):
                continue
            fn = item.get("filename") or item.get("file") or item.get("path")
            tid = item.get("id") or item.get("taxonomy_id") or item.get("classification")
            if fn and tid:
                out[fn] = tid
    elif isinstance(arts, dict):
        for fn, v in arts.items():
            if isinstance(v, str):
                out[fn] = v
            elif isinstance(v, dict):
                tid = v.get("id") or v.get("taxonomy_id") or v.get("classification")
                if tid:
                    out[fn] = tid
    return out


def _score_for_id(taxonomy_entries: list[dict[str, Any]], tid: str) -> float | None:
    for entry in taxonomy_entries:
        if entry.get("id") == tid:
            return float(entry.get("score", 0))
    return None


def _file_score(
    file_taxonomies: dict[str, dict[str, Any]],
    file_path: str,
    tid: str,
) -> float | None:
    """Resolve a per-file classification id to a score.

    Uses basename + suffix matching so ``fitz_sage/engines/fitz_krag/engine.py``
    maps to the ``engine.py`` taxonomy entry.
    """
    for tax_file, tax in file_taxonomies.items():
        if file_path == tax_file or file_path.endswith("/" + tax_file):
            return _score_for_id(tax.get("entries", []), tid)
    return None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(
    classifications: list[dict[str, Any]],
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    """Turn per-plan classifications into a Tier-2 summary.

    Plan score = 0.5 * arch_score + 0.5 * mean(file_scores).
    When no file-taxonomy scores resolve, arch_score alone is used.
    """
    arch_entries = taxonomy.get("architecture_taxonomy", {}).get("entries", [])
    file_tax = taxonomy.get("file_taxonomies", {})

    per_plan: list[dict[str, Any]] = []
    arch_ids: list[str] = []
    file_ids_by_tax: dict[str, list[str]] = {k: [] for k in file_tax}

    for row in classifications:
        plan = row["plan"]
        if not row.get("ok"):
            per_plan.append({"plan": plan, "ok": False, "error": row.get("error")})
            continue
        cls = row["classification"]
        arch_id = _pluck_arch_id(cls)
        arch_score = _score_for_id(arch_entries, arch_id) if arch_id else None
        if arch_id:
            arch_ids.append(arch_id)

        file_ids = _pluck_file_ids(cls)
        file_scores: list[float] = []
        resolved_files: dict[str, dict[str, Any]] = {}
        for fp, tid in file_ids.items():
            s = _file_score(file_tax, fp, tid)
            if s is not None:
                file_scores.append(s)
                resolved_files[fp] = {"id": tid, "score": s}
                for tax_name in file_tax:
                    if fp == tax_name or fp.endswith("/" + tax_name):
                        file_ids_by_tax[tax_name].append(tid)
                        break

        if arch_score is not None and file_scores:
            plan_score = 0.5 * arch_score + 0.5 * (sum(file_scores) / len(file_scores))
        elif arch_score is not None:
            plan_score = arch_score
        elif file_scores:
            plan_score = sum(file_scores) / len(file_scores)
        else:
            plan_score = None

        per_plan.append(
            {
                "plan": plan,
                "ok": True,
                "architecture": {"id": arch_id, "score": arch_score},
                "files": resolved_files,
                "plan_score": plan_score,
            }
        )

    valid_plan_scores = [p["plan_score"] for p in per_plan if p.get("ok") and p.get("plan_score") is not None]
    avg = sum(valid_plan_scores) / len(valid_plan_scores) if valid_plan_scores else None

    def _counts(ids: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in ids:
            out[i] = out.get(i, 0) + 1
        return out

    return {
        "plans_scored": len(valid_plan_scores),
        "plans_failed": sum(1 for p in per_plan if not p.get("ok")),
        "taxonomy_average": round(avg, 1) if avg is not None else None,
        "architecture_distribution": _counts(arch_ids),
        "file_id_distribution": {k: _counts(v) for k, v in file_ids_by_tax.items()},
        "per_plan": per_plan,
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_report(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Tier-2 Taxonomy Scoring\n")
    lines.append("## Summary\n")
    lines.append(f"- Plans scored: {summary['plans_scored']}")
    if summary["plans_failed"]:
        lines.append(f"- Plans failed: {summary['plans_failed']}")
    if summary["taxonomy_average"] is not None:
        lines.append(f"- Taxonomy average: **{summary['taxonomy_average']}/100**")

    if summary["architecture_distribution"]:
        lines.append("\n## Architecture distribution\n")
        for aid, n in sorted(summary["architecture_distribution"].items()):
            lines.append(f"- {aid}: {n}")

    for tax_name, dist in summary["file_id_distribution"].items():
        if not dist:
            continue
        lines.append(f"\n## {tax_name} distribution\n")
        for tid, n in sorted(dist.items()):
            lines.append(f"- {tid}: {n}")

    lines.append("\n## Per-plan\n")
    lines.append("| Plan | Arch | Arch score | Files | Plan score |")
    lines.append("|------|------|-----------:|-------|-----------:|")
    for p in sorted(summary["per_plan"], key=lambda x: x["plan"]):
        if not p.get("ok"):
            lines.append(f"| plan_{p['plan']} | ERROR | — | — | — |")
            continue
        arch_id = p["architecture"]["id"] or "—"
        arch_score = p["architecture"]["score"]
        file_summary = ", ".join(f"{Path(k).name}:{v['id']}" for k, v in p["files"].items())
        score = p.get("plan_score")
        lines.append(
            f"| plan_{p['plan']} | {arch_id} | {arch_score if arch_score is not None else '—'} | "
            f"{file_summary or '—'} | {score:.1f} |" if score is not None else
            f"| plan_{p['plan']} | {arch_id} | {arch_score or '—'} | {file_summary or '—'} | — |"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_tier2_async(
    plan_dir: Path,
    taxonomy: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT_S,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    prompts = sorted(plan_dir.glob("score_v2_prompt_*.md"))
    if not prompts:
        logger.info("tier2: no score_v2_prompt_*.md files — skipping")
        return {}
    logger.info(f"tier2: scoring {len(prompts)} plans via claude -p (model={model}, max_concurrent={concurrency})")

    sem = asyncio.Semaphore(concurrency)
    tasks = [_score_one_plan(p, model, timeout, sem) for p in prompts]
    classifications = await asyncio.gather(*tasks)
    summary = aggregate(classifications, taxonomy)
    return summary


def run_tier2(
    plan_dir: Path,
    taxonomy: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT_S,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    """Sync wrapper around ``run_tier2_async`` for CLI callers."""
    return asyncio.run(run_tier2_async(plan_dir, taxonomy, model, timeout, concurrency))


def write_outputs(plan_dir: Path, summary: dict[str, Any]) -> None:
    """Persist the Tier-2 summary alongside Tier-1 outputs and merge into scores_v2.json."""
    (plan_dir / "scores_v2_taxonomy.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (plan_dir / "SCORE_V2_TAXONOMY.md").write_text(format_report(summary), encoding="utf-8")

    # Merge into scores_v2.json so both tiers live in one place.
    scores_path = plan_dir / "scores_v2.json"
    if scores_path.exists():
        data = json.loads(scores_path.read_text(encoding="utf-8"))
        data["taxonomy_average"] = summary.get("taxonomy_average")
        data["taxonomy_plans_scored"] = summary.get("plans_scored")
        data["taxonomy_plans_failed"] = summary.get("plans_failed")
        data["architecture_distribution"] = summary.get("architecture_distribution")
        scores_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
