# benchmarks/end_to_end_cost.py
"""End-to-end feature cost: pure Claude Code vs fitz-forge plan + Claude Code.

Two arms, each running against a fresh git worktree of the target
codebase so they don't interfere. Both get full Claude Code tool
access (read/write/bash) and are instructed to implement + verify.

* Arm A — Pure Claude Code: user task only. Agent must discover the
  codebase, decide an approach, implement it, and verify. This is what
  a user pays today when they ask Claude Code to add a feature.
* Arm B — fitz-forge plan + Claude Code: the same user task, but we
  also feed a fitz-forge plan (JSON) for reference. The plan already
  contains artifacts with full code bodies, a roadmap, and ADRs, so
  implementation reduces to transcription + integration + testing.

We measure tokens, cost (USD), and wall time per arm. The agent's own
self-report is the success signal — we don't run a separate pytest,
since the agent already runs tests as part of its loop.

Example:

    python -m benchmarks.end_to_end_cost \\
      --source-git-root ../fitz-sage \\
      --base-ref main \\
      --task-file benchmarks/challenges/streaming_implementation/user_prompt.txt \\
      --plan-json benchmarks/challenges/streaming_implementation/results/2026-04-19_15-15-11_run_051/plan_01.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import typer

sys.stderr.write("")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("bench")

app = typer.Typer(no_args_is_help=True)


_PRICE_INPUT_PER_MTOK = 3.00
_PRICE_OUTPUT_PER_MTOK = 15.00
_PRICE_CACHE_WRITE_PER_MTOK = 3.75
_PRICE_CACHE_READ_PER_MTOK = 0.30


def _cost_usd(usage: dict) -> float:
    return (
        usage.get("input_tokens", 0) / 1_000_000 * _PRICE_INPUT_PER_MTOK
        + usage.get("output_tokens", 0) / 1_000_000 * _PRICE_OUTPUT_PER_MTOK
        + usage.get("cache_creation_input_tokens", 0) / 1_000_000 * _PRICE_CACHE_WRITE_PER_MTOK
        + usage.get("cache_read_input_tokens", 0) / 1_000_000 * _PRICE_CACHE_READ_PER_MTOK
    )


def _extract_usage(parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        return {}
    if isinstance(parsed.get("usage"), dict):
        return parsed["usage"]
    msg = parsed.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        return msg["usage"]
    return {}


def _extract_result_text(parsed: dict) -> str:
    if not isinstance(parsed, dict):
        return ""
    if isinstance(parsed.get("result"), str):
        return parsed["result"]
    return ""


def _prepare_worktree(source_git_root: Path, worktree_dir: Path, base_ref: str) -> None:
    """Wipe any prior worktree at ``worktree_dir`` and recreate it at ``base_ref``."""
    # Remove existing worktree registration if present.
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=str(source_git_root),
        check=False,
        capture_output=True,
    )
    # Some stale dir may linger without git knowing about it.
    if worktree_dir.exists():
        # Git refuses to reuse a non-empty dir; do a manual clean.
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(source_git_root),
            check=False,
            capture_output=True,
        )
        import shutil
        shutil.rmtree(worktree_dir, ignore_errors=True)

    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_dir), base_ref],
        cwd=str(source_git_root),
        check=True,
    )
    logger.info(f"worktree ready at {worktree_dir} (from {base_ref})")


def _build_arm_a_prompt(task: str) -> str:
    return (
        "You are implementing a feature in the repository at your current "
        "working directory. You have full tool access (Read, Edit, Write, "
        "Bash, etc.).\n\n"
        f"User task:\n{task}\n\n"
        "Do the following:\n"
        "1. Explore the codebase enough to understand what to change.\n"
        "2. Implement the feature end-to-end.\n"
        "3. Run the project's test suite and iterate until it passes.\n"
        "4. At the end of your response, report whether tests pass and "
        "list the files you modified.\n"
    )


def _plan_to_markdown(plan: dict) -> str:
    """Render plan JSON to a readable markdown plan for the agent to consume."""
    lines: list[str] = ["# Implementation Plan\n"]

    arch = plan.get("architecture", {}) or {}
    if arch.get("recommended"):
        lines.append("## Recommended Architecture\n")
        lines.append(f"{arch['recommended']}\n")
    if arch.get("reasoning"):
        lines.append("## Reasoning\n")
        lines.append(f"{arch['reasoning']}\n")

    design = plan.get("design", {}) or {}
    artifacts = design.get("artifacts") or []
    if artifacts:
        lines.append("## Artifacts (files to create or modify)\n")
        for art in artifacts:
            filename = art.get("filename") or art.get("path") or "(unnamed)"
            purpose = art.get("purpose") or ""
            content = art.get("content") or ""
            lines.append(f"### {filename}\n")
            if purpose:
                lines.append(f"**Purpose:** {purpose}\n")
            if content:
                lines.append(f"```python\n{content}\n```\n")

    components = design.get("components") or []
    if components:
        lines.append("## Components\n")
        for c in components:
            name = c.get("name", "")
            desc = c.get("description", c.get("purpose", ""))
            lines.append(f"- **{name}**: {desc}")
        lines.append("")

    roadmap = plan.get("roadmap", {}) or {}
    phases = roadmap.get("phases") or []
    if phases:
        lines.append("## Roadmap\n")
        for i, phase in enumerate(phases, 1):
            name = phase.get("name", f"Phase {i}")
            desc = phase.get("description", "")
            lines.append(f"### Phase {i}: {name}\n")
            if desc:
                lines.append(f"{desc}\n")
            verif = phase.get("verification") or phase.get("verification_commands") or []
            if verif:
                lines.append("**Verification:**")
                for v in verif:
                    lines.append(f"- {v}")
                lines.append("")

    risks = (plan.get("risk", {}) or {}).get("risks") or []
    if risks:
        lines.append("## Risks\n")
        for r in risks:
            desc = r.get("description", "") if isinstance(r, dict) else str(r)
            mitigation = r.get("mitigation", "") if isinstance(r, dict) else ""
            lines.append(f"- {desc}" + (f" — mitigation: {mitigation}" if mitigation else ""))
        lines.append("")

    return "\n".join(lines)


def _build_arm_b_prompt(task: str, plan_markdown: str) -> str:
    return (
        "You are implementing a feature in the repository at your current "
        "working directory. You have full tool access (Read, Edit, Write, "
        "Bash, etc.).\n\n"
        f"User task:\n{task}\n\n"
        "Below is a pre-generated implementation plan (produced by "
        "fitz-forge running a local LLM). It contains recommended "
        "architecture, artifacts with full code bodies, a phased roadmap, "
        "and risks. Use it as your primary reference.\n\n"
        "-----\n\n"
        f"{plan_markdown}\n\n"
        "-----\n\n"
        "Do the following:\n"
        "1. Read and follow the plan. Treat artifact bodies as the "
        "intended implementation; adapt only where they don't match the "
        "real codebase.\n"
        "2. Implement the feature end-to-end.\n"
        "3. Run the project's test suite and iterate until it passes.\n"
        "4. At the end of your response, report whether tests pass and "
        "list the files you modified.\n"
    )


async def _run_arm(
    arm_label: str,
    worktree_dir: Path,
    prompt: str,
    timeout_s: int,
    out_dir: Path,
) -> dict:
    """Run a single Claude Code arm in ``worktree_dir``. Captures cost + output."""
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    logger.info(f"arm {arm_label}: launching claude in {worktree_dir} (timeout={timeout_s}s)")
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(worktree_dir),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error(f"arm {arm_label}: timed out after {timeout_s}s")
        return {"arm": arm_label, "success": False, "error": "timeout"}

    elapsed = time.monotonic() - t0
    raw = stdout.decode("utf-8", "replace")
    (out_dir / f"arm_{arm_label}_raw.json").write_text(raw, encoding="utf-8")

    if proc.returncode != 0:
        logger.error(
            f"arm {arm_label}: exit {proc.returncode}: "
            f"{stderr.decode('utf-8', 'replace')[:400]}"
        )
        return {
            "arm": arm_label,
            "success": False,
            "error": f"exit {proc.returncode}",
            "elapsed_s": round(elapsed, 1),
        }

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"arm": arm_label, "success": False, "error": f"json_decode: {e}"}

    usage = _extract_usage(parsed)
    cost = _cost_usd(usage)
    result_text = _extract_result_text(parsed)
    (out_dir / f"arm_{arm_label}_result.md").write_text(
        result_text or "(no result text)", encoding="utf-8"
    )

    # Summarize file changes inside the worktree for the comparison table.
    git_diff_stat = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=str(worktree_dir),
        capture_output=True,
        text=True,
    )
    changed_files_stat = git_diff_stat.stdout
    (out_dir / f"arm_{arm_label}_diff_stat.txt").write_text(
        changed_files_stat, encoding="utf-8"
    )

    logger.info(
        f"arm {arm_label}: {elapsed:.1f}s, "
        f"input={usage.get('input_tokens', 0):,} output={usage.get('output_tokens', 0):,} "
        f"cache_w={usage.get('cache_creation_input_tokens', 0):,} "
        f"cache_r={usage.get('cache_read_input_tokens', 0):,} "
        f"-> ${cost:.4f}"
    )

    return {
        "arm": arm_label,
        "success": True,
        "elapsed_s": round(elapsed, 1),
        "usage": usage,
        "cost_usd": round(cost, 4),
        "result_chars": len(result_text or ""),
        "diff_stat": changed_files_stat,
    }


@app.command()
def run(
    source_git_root: str = typer.Option(..., help="Git root of the target codebase (main checkout)"),
    base_ref: str = typer.Option("main", help="Git ref that each worktree starts from"),
    task_file: str = typer.Option(..., help="Path to user_prompt.txt for the feature"),
    plan_json: str = typer.Option(..., help="fitz-forge plan JSON for Arm B"),
    out_root: str = typer.Option("", help="Output root (default: benchmarks/end_to_end_results/)"),
    timeout_s: int = typer.Option(2700, help="Per-arm timeout in seconds (default 45min)"),
    skip_arm: str = typer.Option("", help="Skip an arm ('a' or 'b') — useful for re-runs"),
) -> None:
    """Measure end-to-end feature cost: pure Claude Code vs fitz-forge plan + Claude Code."""
    src_root = Path(source_git_root).resolve()
    if not (src_root / ".git").exists():
        raise typer.BadParameter(f"not a git root: {src_root}")

    task = Path(task_file).read_text(encoding="utf-8").strip()
    plan = json.loads(Path(plan_json).read_text(encoding="utf-8"))
    plan_md = _plan_to_markdown(plan)

    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = Path(out_root) if out_root else Path("benchmarks/end_to_end_results") / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan_used.md").write_text(plan_md, encoding="utf-8")
    logger.info(f"output dir: {out_dir}")

    results: list[dict] = []

    async def _execute() -> None:
        # Arm A: pure Claude Code
        if skip_arm != "a":
            wt_a = src_root.parent / f"{src_root.name}-arm-a"
            _prepare_worktree(src_root, wt_a, base_ref)
            prompt_a = _build_arm_a_prompt(task)
            (out_dir / "arm_a_prompt.md").write_text(prompt_a, encoding="utf-8")
            r_a = await _run_arm("a", wt_a, prompt_a, timeout_s, out_dir)
            results.append(r_a)
        else:
            logger.info("skipping arm A")

        # Arm B: fitz-forge plan + Claude Code
        if skip_arm != "b":
            wt_b = src_root.parent / f"{src_root.name}-arm-b"
            _prepare_worktree(src_root, wt_b, base_ref)
            prompt_b = _build_arm_b_prompt(task, plan_md)
            (out_dir / "arm_b_prompt.md").write_text(prompt_b, encoding="utf-8")
            r_b = await _run_arm("b", wt_b, prompt_b, timeout_s, out_dir)
            results.append(r_b)
        else:
            logger.info("skipping arm B")

    asyncio.run(_execute())
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )

    # Comparison summary.
    by_arm = {r["arm"]: r for r in results if r.get("success")}
    a = by_arm.get("a", {})
    b = by_arm.get("b", {})
    a_cost = a.get("cost_usd")
    b_cost = b.get("cost_usd")

    lines = [
        "# End-to-end Feature Cost — Pure Claude Code vs fitz-forge plan + Claude Code",
        "",
        f"Task file: `{task_file}`",
        f"Plan used: `{plan_json}`",
        "",
        "| Arm | Wall time | Input | Output | Cache write | Cache read | Cost (USD) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, r in (("A — Pure Claude Code", a), ("B — fitz-forge + Claude Code", b)):
        if not r:
            lines.append(f"| {label} | (missing) | — | — | — | — | — |")
            continue
        u = r.get("usage", {})
        lines.append(
            f"| {label} | {r.get('elapsed_s', 0):.0f}s | "
            f"{u.get('input_tokens', 0):,} | {u.get('output_tokens', 0):,} | "
            f"{u.get('cache_creation_input_tokens', 0):,} | "
            f"{u.get('cache_read_input_tokens', 0):,} | "
            f"${r.get('cost_usd', 0):.4f} |"
        )
    lines.append("")
    if a_cost is not None and b_cost is not None:
        savings = a_cost - b_cost
        pct = (savings / a_cost * 100) if a_cost else 0
        lines.append(
            f"**Savings per feature:** ${savings:.2f} "
            f"({pct:.0f}% cheaper with fitz-forge)"
        )
        lines.append("")

    lines.append("## Pricing assumption")
    lines.append(f"- Input: ${_PRICE_INPUT_PER_MTOK:.2f}/MTok")
    lines.append(f"- Output: ${_PRICE_OUTPUT_PER_MTOK:.2f}/MTok")
    lines.append(f"- Cache write: ${_PRICE_CACHE_WRITE_PER_MTOK:.2f}/MTok")
    lines.append(f"- Cache read: ${_PRICE_CACHE_READ_PER_MTOK:.2f}/MTok")

    summary = "\n".join(lines)
    (out_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    app()
