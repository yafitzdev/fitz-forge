# benchmarks/claude_code_benchmark.py
"""Benchmark arm: cold Claude Code agents with the same input as raw gemma.

Companion to ``no_harness.py``. Same interface, same prompt-building,
same parsing, same V2 scoring path — only difference is that the
generation call shells out to ``claude -p`` instead of the local LLM
client. Tools are disabled so the agent works only from the file
content embedded in the prompt (exact parity with the raw-gemma arm,
which also had no file-reading capability beyond its prompt).

The three benchmark arms thus share a single methodology:

    no_harness.py              raw local model, files in prompt
    plan_factory.py decomposed local model + fitz-forge harness
    claude_code_benchmark.py   frontier model (Sonnet), files in prompt

All three produce plan_NN.json in the same shape, all three get
scored by the V2 scorer, all three slot into the same Benchmarks
table on equal terms.

Example:

    python -m benchmarks.claude_code_benchmark \\
      --source-dir ../fitz-sage \\
      --context-file benchmarks/challenges/streaming_implementation/ideal_context.json \\
      --query "$(cat benchmarks/challenges/streaming_implementation/user_prompt.txt)" \\
      --taxonomy benchmarks/challenges/streaming_implementation/taxonomy.json \\
      --runs 5 --score-v2
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import typer

from .no_harness import (
    _build_user_prompt,
    _load_source_file,
    _parse_response_to_artifacts,
)

sys.stderr.write("")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("bench")

app = typer.Typer(no_args_is_help=True)


_MAX_FILE_BYTES_DEFAULT = 6_000
_MAX_PROMPT_CHARS_DEFAULT = 200_000
_DEFAULT_TIMEOUT_S = 900


# Sonnet 4.6 public pricing, $/MTok.
_PRICE_INPUT_PER_MTOK = 3.00
_PRICE_OUTPUT_PER_MTOK = 15.00
_PRICE_CACHE_WRITE_PER_MTOK = 3.75
_PRICE_CACHE_READ_PER_MTOK = 0.30


# The same system prompt no_harness.py uses, promoted to force pure
# generation (no tool use). Claude Code with --disallowedTools set to the
# common read tools still lets the model text-generate; the instruction
# reinforces that we want the plan from the embedded content only.
_SYSTEM_PROMPT = (
    "You are a senior software engineer. A user has a task to complete "
    "on an existing codebase. Given the user's request and the relevant "
    "source files embedded in the message below, produce a complete "
    "implementation plan. For every file you need to create or modify, "
    "output a section in this exact shape:\n\n"
    "## path/to/file.py\n"
    "```python\n"
    "<full file content or the complete modified function / class>\n"
    "```\n\n"
    "Rules:\n"
    "1. Real filenames only — paths exactly as they appear in the "
    "provided source.\n"
    "2. Real code only — full, runnable implementation, not pseudocode.\n"
    "3. Cover every file that needs a change.\n"
    "4. Be specific about field names and method signatures — not "
    "'record the signals' but the actual dict keys / parameter names.\n"
    "5. Do NOT use any tools. Work only from the content provided in "
    "this user message.\n\n"
    "Do not output anything outside the '## filename' + code-fence "
    "structure. No preamble, no summary at the end."
)


_DISALLOWED_TOOLS = [
    "Read",
    "Bash",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    "Task",
    "Agent",
    "ExitPlanMode",
]


def _cost_usd(usage: dict) -> float:
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    return (
        input_tokens / 1_000_000 * _PRICE_INPUT_PER_MTOK
        + output_tokens / 1_000_000 * _PRICE_OUTPUT_PER_MTOK
        + cache_creation / 1_000_000 * _PRICE_CACHE_WRITE_PER_MTOK
        + cache_read / 1_000_000 * _PRICE_CACHE_READ_PER_MTOK
    )


def _extract_usage(parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        return {}
    if isinstance(parsed.get("usage"), dict):
        return parsed["usage"]
    msg = parsed.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        return msg["usage"]
    if isinstance(parsed.get("total_tokens_input"), int):
        return {
            "input_tokens": parsed.get("total_tokens_input", 0),
            "output_tokens": parsed.get("total_tokens_output", 0),
            "cache_creation_input_tokens": parsed.get("total_cache_creation_tokens", 0),
            "cache_read_input_tokens": parsed.get("total_cache_read_tokens", 0),
        }
    return {}


def _extract_response_text(parsed: dict) -> str:
    if not isinstance(parsed, dict):
        return ""
    if isinstance(parsed.get("result"), str):
        return parsed["result"]
    msg = parsed.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join(pieces)
    return ""


async def _one_run(
    run_id: int,
    source_dir: Path,
    file_list: list[str],
    query: str,
    out_dir: Path,
    max_file_bytes: int,
    max_prompt_chars: int,
    timeout_s: int,
) -> dict:
    """One parallel Claude Code invocation; writes plan_NN.json in place."""
    file_contents: dict[str, str] = {}
    for rel in file_list:
        content = _load_source_file(source_dir, rel, max_file_bytes)
        if content:
            file_contents[rel] = content

    user_prompt = _build_user_prompt(query, file_contents, max_prompt_chars)
    full_prompt = (
        _SYSTEM_PROMPT
        + "\n\n-----\n\n"
        + user_prompt
    )

    # Prompt goes on stdin — Windows argv caps at ~8 KB, far below the
    # typical ~100 KB multi-file prompt. Same technique score_taxonomy.py
    # uses for Sonnet grader prompts.
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--disallowed-tools",
        ",".join(_DISALLOWED_TOOLS),
    ]

    logger.info(
        f"run {run_id}: {len(file_contents)} files, {len(full_prompt)} chars in prompt"
    )
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=full_prompt.encode("utf-8")),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error(f"run {run_id}: timed out after {timeout_s}s")
        return {
            "run": run_id,
            "success": False,
            "error": "timeout",
            "elapsed_s": round(time.monotonic() - t0, 1),
        }
    elapsed = time.monotonic() - t0

    if proc.returncode != 0:
        logger.error(
            f"run {run_id}: exit {proc.returncode}: "
            f"{stderr.decode('utf-8', 'replace')[:400]}"
        )
        return {
            "run": run_id,
            "success": False,
            "error": f"exit {proc.returncode}",
            "elapsed_s": round(elapsed, 1),
        }

    raw = stdout.decode("utf-8", "replace")
    (out_dir / f"raw_{run_id:02d}.json").write_text(raw, encoding="utf-8")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"run {run_id}: JSON parse failed: {e}")
        return {
            "run": run_id,
            "success": False,
            "error": f"json_decode: {e}",
            "elapsed_s": round(elapsed, 1),
        }

    usage = _extract_usage(parsed)
    cost = _cost_usd(usage) if usage else 0.0
    plan_text = _extract_response_text(parsed)
    (out_dir / f"raw_{run_id:02d}.md").write_text(
        plan_text or "(no plan text extracted)", encoding="utf-8"
    )

    artifacts = _parse_response_to_artifacts(plan_text)
    logger.info(
        f"run {run_id}: {elapsed:.1f}s, {len(artifacts)} artifacts parsed, "
        f"input={usage.get('input_tokens', 0):,} output={usage.get('output_tokens', 0):,} "
        f"cache_w={usage.get('cache_creation_input_tokens', 0):,} "
        f"cache_r={usage.get('cache_read_input_tokens', 0):,} "
        f"-> ${cost:.4f}"
    )

    plan_data = {
        "context": {"needed_artifacts": [], "key_requirements": []},
        "architecture": {
            "recommended": "",
            "reasoning": plan_text[:2000] if plan_text else "",
            "approaches": [],
        },
        "design": {
            "artifacts": artifacts,
            "adrs": [],
            "components": [],
            "data_model": {},
            "integration_points": [],
        },
        "roadmap": {"phases": []},
        "risk": {"risks": []},
    }
    (out_dir / f"plan_{run_id:02d}.json").write_text(
        json.dumps(plan_data, indent=2), encoding="utf-8"
    )

    return {
        "run": run_id,
        "success": bool(artifacts),
        "artifact_count": len(artifacts),
        "elapsed_s": round(elapsed, 1),
        "usage": usage,
        "cost_usd": round(cost, 4),
        "raw_output_chars": len(plan_text or ""),
        "error": None if artifacts else "no artifacts parsed",
    }


@app.command()
def run(
    source_dir: str = typer.Option(..., help="Target codebase root"),
    context_file: str = typer.Option(..., help="ideal_context.json whose file_list defines the input files"),
    query: str = typer.Option(..., help="Task description / user prompt"),
    taxonomy: str | None = typer.Option(None, help="taxonomy.json (required for --score-v2)"),
    runs: int = typer.Option(5, help="Number of parallel agents"),
    max_file_bytes: int = typer.Option(_MAX_FILE_BYTES_DEFAULT, help="Per-file truncation"),
    max_prompt_chars: int = typer.Option(_MAX_PROMPT_CHARS_DEFAULT, help="Total prompt size cap"),
    timeout_s: int = typer.Option(_DEFAULT_TIMEOUT_S, help="Per-run timeout"),
    score_v2: bool = typer.Option(False, "--score-v2", help="Run V2 scoring after"),
    no_tier2: bool = typer.Option(False, "--no-tier2", help="Skip Tier-2 Sonnet scoring"),
    out_root: str = typer.Option("", help="Output root (default: alongside context_file)"),
) -> None:
    """N cold Claude Code agents, in parallel, same input as raw gemma arm."""
    ctx_path = Path(context_file)
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    file_list: list[str] = ctx.get("file_list") or []
    if not file_list:
        raise typer.BadParameter(f"{context_file} has no 'file_list'")

    challenge_root = ctx_path.parent
    results_root = Path(out_root) if out_root else challenge_root / "results"
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = results_root / f"{ts}_claude_code"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Running {runs} Claude Code agent(s) in parallel -> {out_dir}")

    async def _main() -> list[dict]:
        tasks = [
            _one_run(
                run_id=i + 1,
                source_dir=Path(source_dir),
                file_list=file_list,
                query=query,
                out_dir=out_dir,
                max_file_bytes=max_file_bytes,
                max_prompt_chars=max_prompt_chars,
                timeout_s=timeout_s,
            )
            for i in range(runs)
        ]
        return await asyncio.gather(*tasks)

    results = asyncio.run(_main())
    (out_dir / "runs.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )

    ok = [r for r in results if r.get("success")]
    artifact_counts = [r.get("artifact_count", 0) for r in results]
    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    total_elapsed = sum(r.get("elapsed_s", 0.0) for r in results)
    max_elapsed = max((r.get("elapsed_s", 0.0) for r in results), default=0.0)

    summary_lines = [
        "# Claude Code Benchmark (parallel cold agents)",
        "",
        f"- Runs: {len(ok)}/{len(results)} successful",
        f"- Parallel wall time: {max_elapsed:.1f}s (sum of serial times: {total_elapsed:.1f}s)",
        f"- Cost total: ${total_cost:.4f}",
        f"- Mean cost/run: ${total_cost / max(1, len(results)):.4f}",
        "",
        "## Per-run artifacts",
        f"- min: {min(artifact_counts or [0])}  "
        f"avg: {sum(artifact_counts) / max(1, len(artifact_counts)):.1f}  "
        f"max: {max(artifact_counts or [0])}",
        "",
        "## Pricing assumption",
        f"- Input: ${_PRICE_INPUT_PER_MTOK:.2f}/MTok",
        f"- Output: ${_PRICE_OUTPUT_PER_MTOK:.2f}/MTok",
        f"- Cache write: ${_PRICE_CACHE_WRITE_PER_MTOK:.2f}/MTok",
        f"- Cache read: ${_PRICE_CACHE_READ_PER_MTOK:.2f}/MTok",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(summary_lines), encoding="utf-8")
    print("\n".join(summary_lines))

    if score_v2:
        if not taxonomy:
            raise typer.BadParameter("--score-v2 requires --taxonomy")
        if not ok:
            logger.warning("No successful runs — skipping scoring")
            return
        from .plan_factory import _prepare_scoring_v2

        structural_index = ctx.get("synthesized", "") or ""
        _prepare_scoring_v2(
            str(out_dir),
            query,
            structural_index,
            source_dir=source_dir,
            taxonomy_file=taxonomy,
            skip_tier2=no_tier2,
        )


if __name__ == "__main__":
    app()
