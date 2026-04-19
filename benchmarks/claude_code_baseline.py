# benchmarks/claude_code_baseline.py
"""Baseline: what does pure Claude Code (Sonnet) agentic planning cost?

Invokes ``claude -p`` in headless plan-mode against a target repo with
the same user prompt a fitz-forge benchmark uses, captures the token
usage from the JSON output, and converts to dollars at current Sonnet
pricing. This is the 'what you'd pay if you asked Claude Code to do
this task without any harness' comparison point for the README.

Uses ``--permission-mode plan`` so Claude Code reads files, reasons
about the task, and produces a plan *without* modifying any files —
that's the honest comparator to what fitz-forge does.

    python -m benchmarks.claude_code_baseline \\
      --source-dir ../fitz-sage \\
      --query "$(cat benchmarks/challenges/streaming_implementation/user_prompt.txt)" \\
      --out-dir benchmarks/challenges/streaming_implementation/results
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


# Sonnet 4.6 public pricing, $/MTok. Update if Anthropic changes prices.
_PRICE_INPUT_PER_MTOK = 3.00
_PRICE_OUTPUT_PER_MTOK = 15.00
_PRICE_CACHE_WRITE_PER_MTOK = 3.75
_PRICE_CACHE_READ_PER_MTOK = 0.30


def _cost_usd(usage: dict) -> float:
    """Convert a Claude usage dict to USD at current Sonnet pricing."""
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


async def _one_run(
    run_id: int,
    source_dir: Path,
    query: str,
    out_dir: Path,
    timeout_s: int,
) -> dict:
    """Run Claude Code once in plan mode, capture output + usage."""
    wrapped_prompt = (
        f"{query}\n\n"
        "Produce an implementation plan — list each file to create or modify, "
        "the specific changes in each, and how to verify the result. Do not "
        "modify any files; this is a planning task only."
    )

    cmd = [
        "claude",
        "-p",
        wrapped_prompt,
        "--permission-mode",
        "plan",
        "--output-format",
        "json",
    ]

    logger.info(
        f"claude-code run {run_id}: cwd={source_dir}, timeout={timeout_s}s"
    )
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(source_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error(f"run {run_id}: timed out after {timeout_s}s")
        return {"run": run_id, "success": False, "error": "timeout"}
    elapsed = time.monotonic() - t0

    if proc.returncode != 0:
        logger.error(
            f"run {run_id}: exit {proc.returncode}: "
            f"{stderr.decode('utf-8', 'replace')[:400]}"
        )
        return {"run": run_id, "success": False, "error": f"exit {proc.returncode}"}

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

    # Claude Code JSON output typically carries a 'usage' or nested message
    # usage. Extract flexibly so we don't break on schema drift.
    usage = _extract_usage(parsed)
    if not usage:
        logger.warning(f"run {run_id}: no usage field in JSON output")

    cost = _cost_usd(usage) if usage else 0.0
    plan_text = _extract_response_text(parsed)
    (out_dir / f"plan_{run_id:02d}.md").write_text(
        plan_text or "(no plan text extracted)", encoding="utf-8"
    )

    logger.info(
        f"run {run_id}: {elapsed:.1f}s, "
        f"input={usage.get('input_tokens', 0):,} output={usage.get('output_tokens', 0):,} "
        f"cache_write={usage.get('cache_creation_input_tokens', 0):,} "
        f"cache_read={usage.get('cache_read_input_tokens', 0):,} "
        f"-> ${cost:.4f}"
    )

    return {
        "run": run_id,
        "success": True,
        "elapsed_s": round(elapsed, 1),
        "usage": usage,
        "cost_usd": round(cost, 4),
        "plan_chars": len(plan_text or ""),
    }


def _extract_usage(parsed: dict) -> dict:
    """Pull input/output/cache token counts from Claude Code JSON output."""
    if not isinstance(parsed, dict):
        return {}
    # Direct ``usage`` field.
    if isinstance(parsed.get("usage"), dict):
        return parsed["usage"]
    # Nested inside ``message``.
    msg = parsed.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        return msg["usage"]
    # Some versions emit a ``total_cost_usd`` + ``num_turns`` at top level;
    # in that case usage may not be exposed. Return what we can.
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


@app.command()
def run(
    source_dir: str = typer.Option(..., help="Target codebase root (cwd for claude)"),
    query: str = typer.Option(..., help="Task description / user prompt"),
    runs: int = typer.Option(1, help="Number of runs"),
    timeout_s: int = typer.Option(900, help="Per-run timeout (s)"),
    out_root: str = typer.Option("", help="Output root (default: benchmarks/claude_code_results/)"),
) -> None:
    """Pure Claude Code planning baseline — what you'd pay without the harness."""
    src = Path(source_dir).resolve()
    if not src.exists():
        raise typer.BadParameter(f"source_dir does not exist: {src}")

    root = Path(out_root) if out_root else Path("benchmarks/claude_code_results")
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / f"{ts}_claude_code"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Running {runs} Claude Code baseline run(s) -> {out_dir}")

    async def _main() -> list[dict]:
        results: list[dict] = []
        for i in range(runs):
            r = await _one_run(
                run_id=i + 1,
                source_dir=src,
                query=query,
                out_dir=out_dir,
                timeout_s=timeout_s,
            )
            results.append(r)
        return results

    results = asyncio.run(_main())
    (out_dir / "runs.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("success")]
    if not ok:
        logger.error("All runs failed")
        return

    total_cost = sum(r.get("cost_usd", 0.0) for r in ok)
    total_elapsed = sum(r.get("elapsed_s", 0.0) for r in ok)
    total_in = sum(r.get("usage", {}).get("input_tokens", 0) for r in ok)
    total_out = sum(r.get("usage", {}).get("output_tokens", 0) for r in ok)
    total_cache_w = sum(r.get("usage", {}).get("cache_creation_input_tokens", 0) for r in ok)
    total_cache_r = sum(r.get("usage", {}).get("cache_read_input_tokens", 0) for r in ok)

    summary = [
        "# Claude Code Baseline (no harness)",
        "",
        f"- Runs: {len(ok)}/{len(results)} successful",
        f"- Mean latency: {total_elapsed / len(ok):.1f}s",
        f"- Mean cost:    ${total_cost / len(ok):.4f}",
        "",
        "## Token totals (across all successful runs)",
        "",
        f"- Input:       {total_in:,}",
        f"- Output:      {total_out:,}",
        f"- Cache write: {total_cache_w:,}",
        f"- Cache read:  {total_cache_r:,}",
        f"- Cost total:  ${total_cost:.4f}",
        "",
        "## Pricing assumption",
        "",
        f"- Input: ${_PRICE_INPUT_PER_MTOK:.2f}/MTok",
        f"- Output: ${_PRICE_OUTPUT_PER_MTOK:.2f}/MTok",
        f"- Cache write: ${_PRICE_CACHE_WRITE_PER_MTOK:.2f}/MTok",
        f"- Cache read: ${_PRICE_CACHE_READ_PER_MTOK:.2f}/MTok",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(summary), encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    app()
