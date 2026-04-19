# benchmarks/no_harness.py
"""Baseline benchmark: raw local LLM, no fitz-forge pipeline.

Feeds the model exactly the same files and task description that the
harness run sees (``ideal_context.json`` file_list), in a single prompt,
and asks for a plan. Parses the markdown-with-code-fences response into
the minimal PlanOutput shape the V2 scorer consumes, then runs
Tier-1 deterministic + Tier-2 taxonomy scoring the same way the harness
benchmark does.

The comparison isolates one variable: the presence of the fitz-forge
harness (pipeline + reviews + cascading). Same model, same files, same
scorer — different harness.

    python -m benchmarks.no_harness \\
      --source-dir ../fitz-sage \\
      --context-file benchmarks/challenges/streaming_implementation/ideal_context.json \\
      --query "$(cat benchmarks/challenges/streaming_implementation/user_prompt.txt)" \\
      --taxonomy benchmarks/challenges/streaming_implementation/taxonomy.json \\
      --runs 1 \\
      --score-v2
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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


_MAX_FILE_BYTES_DEFAULT = 6_000
_MAX_PROMPT_CHARS_DEFAULT = 200_000
_MAX_TOKENS_DEFAULT = 16_384


_SYSTEM_PROMPT = (
    "You are a senior software engineer. A user has a task to complete on "
    "an existing codebase. Given the user's request and the relevant "
    "source files, produce a complete implementation plan. For every file "
    "you need to create or modify, output a section in this exact shape:\n\n"
    "## path/to/file.py\n"
    "```python\n"
    "<full file content or the complete modified function / class>\n"
    "```\n\n"
    "Rules:\n"
    "1. Real filenames only — use paths exactly as they appear in the "
    "provided source (including directory components).\n"
    "2. Real code only — full, runnable implementation, not pseudocode "
    "or placeholders like '# implement me'.\n"
    "3. Cover every file that needs a change. If the call chain touches "
    "five files, emit all five.\n"
    "4. Be specific about field names and method signatures — not "
    "'record the signals' but the actual dict keys / parameter names.\n\n"
    "Do not output anything outside the '## filename' + code-fence "
    "structure. No preamble, no summary at the end."
)


def _load_source_file(source_dir: Path, relpath: str, max_bytes: int) -> str:
    """Read a file relative to the source dir, truncated to ``max_bytes``."""
    full = source_dir / relpath
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"skipped {relpath}: {e}")
        return ""
    if len(text) > max_bytes:
        text = text[:max_bytes] + f"\n\n# … truncated to {max_bytes} bytes …\n"
    return text


def _build_user_prompt(
    query: str,
    file_contents: dict[str, str],
    max_prompt_chars: int,
) -> str:
    """Assemble the single-shot prompt, stopping before total chars blow the budget.

    We intentionally keep file ORDER from the ideal context — the list is
    already ranked by relevance for the task — so truncation happens at
    the tail (least-relevant files) rather than mid-chain.
    """
    header = f"## User task\n\n{query.strip()}\n\n## Relevant source files\n\n"
    footer = (
        "\n## Produce the implementation plan now\n\n"
        "Remember: one `## filename` heading per file you touch, "
        "followed by a fenced code block containing the full "
        "content / modified regions of that file."
    )
    budget = max_prompt_chars - len(header) - len(footer) - 500
    body_parts: list[str] = []
    used = 0
    kept = 0
    dropped: list[str] = []
    for path, content in file_contents.items():
        if not content.strip():
            continue
        block = f"### {path}\n```\n{content}\n```\n\n"
        if used + len(block) > budget:
            dropped.append(path)
            continue
        body_parts.append(block)
        used += len(block)
        kept += 1
    if dropped:
        body_parts.append(
            "### (additional files not shown — prompt budget exhausted)\n"
            + "\n".join(f"- {p}" for p in dropped)
            + "\n\n"
        )
    logger.info(
        f"prompt assembly: kept {kept} / {kept + len(dropped)} files "
        f"({used} body chars within {budget} budget)"
    )
    return header + "".join(body_parts) + footer


_FILE_SECTION_RE = re.compile(
    r"^##\s+([^\n`#]+?)\s*$\n+```[a-zA-Z0-9_+-]*\s*\n(.*?)\n```",
    re.DOTALL | re.MULTILINE,
)


def _parse_response_to_artifacts(raw: str) -> list[dict]:
    """Extract ``## filename`` + fenced code blocks into artifact dicts.

    Lenient by design — the raw model output may include preamble or
    explanatory prose between sections. We only capture `## filename`
    headings followed by a code fence.
    """
    artifacts: list[dict] = []
    for match in _FILE_SECTION_RE.finditer(raw):
        filename = match.group(1).strip().strip("`")
        content = match.group(2)
        if not filename or not content.strip():
            continue
        # Skip section headings that aren't file paths (e.g. "## Summary").
        if "/" not in filename and filename not in ("__init__.py",) and not filename.endswith(
            (".py", ".ts", ".js", ".tsx", ".jsx", ".yaml", ".yml", ".json", ".sql", ".md")
        ):
            continue
        artifacts.append(
            {
                "filename": filename,
                "content": content,
                "purpose": "",
            }
        )
    return artifacts


async def _one_run(
    run_id: int,
    source_dir: Path,
    file_list: list[str],
    query: str,
    out_dir: Path,
    max_file_bytes: int,
    max_prompt_chars: int,
    max_tokens: int,
) -> dict:
    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.llm.generate import configure_tracing, generate

    config = load_config()
    client = create_llm_client(config)
    if hasattr(client, "health_check"):
        await client.health_check()

    trace_dir = out_dir / f"traces_{run_id:02d}"
    configure_tracing(trace_dir)

    file_contents: dict[str, str] = {}
    for rel in file_list:
        content = _load_source_file(source_dir, rel, max_file_bytes)
        if content:
            file_contents[rel] = content

    user_prompt = _build_user_prompt(query, file_contents, max_prompt_chars)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    prompt_bytes = sum(len(m["content"]) for m in messages)
    logger.info(
        f"no-harness run {run_id}: {len(file_contents)} files, "
        f"{prompt_bytes} chars in prompt"
    )

    t0 = time.monotonic()
    try:
        raw = await generate(
            client,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
            label="no_harness_plan",
        )
        elapsed = time.monotonic() - t0
    except Exception as e:
        logger.error(f"generation failed: {e}")
        configure_tracing(None)
        return {
            "run": run_id,
            "success": False,
            "error": str(e),
            "elapsed_s": round(time.monotonic() - t0, 1),
        }
    finally:
        configure_tracing(None)

    # Persist the raw response for inspection.
    (out_dir / f"raw_{run_id:02d}.md").write_text(raw, encoding="utf-8")

    artifacts = _parse_response_to_artifacts(raw)
    logger.info(
        f"no-harness run {run_id}: parsed {len(artifacts)} artifact(s) "
        f"in {elapsed:.1f}s ({len(raw)} chars output)"
    )

    plan_data = {
        "context": {"needed_artifacts": [], "key_requirements": []},
        "architecture": {
            "recommended": "",
            "reasoning": raw[:2000],
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

    plan_path = out_dir / f"plan_{run_id:02d}.json"
    plan_path.write_text(json.dumps(plan_data, indent=2), encoding="utf-8")

    return {
        "run": run_id,
        "success": bool(artifacts),
        "artifact_count": len(artifacts),
        "plan_size": len(json.dumps(plan_data)),
        "raw_output_chars": len(raw),
        "elapsed_s": round(elapsed, 1),
        "error": None if artifacts else "no artifacts parsed from response",
    }


@app.command()
def run(
    source_dir: str = typer.Option(..., help="Target codebase root"),
    context_file: str = typer.Option(..., help="ideal_context.json whose file_list defines the input files"),
    query: str = typer.Option(..., help="Task description / user prompt"),
    taxonomy: str | None = typer.Option(None, help="taxonomy.json (required for --score-v2)"),
    runs: int = typer.Option(1, help="Number of runs"),
    max_file_bytes: int = typer.Option(_MAX_FILE_BYTES_DEFAULT, help="Per-file truncation"),
    max_prompt_chars: int = typer.Option(_MAX_PROMPT_CHARS_DEFAULT, help="Total prompt size cap"),
    max_tokens: int = typer.Option(_MAX_TOKENS_DEFAULT, help="LLM max output tokens"),
    score_v2: bool = typer.Option(False, "--score-v2", help="Run V2 scoring (T1 + T2) after"),
    no_tier2: bool = typer.Option(False, "--no-tier2", help="Skip Tier-2 taxonomy scoring"),
    out_root: str = typer.Option("", help="Output root dir (default: alongside context_file)"),
) -> None:
    """One-prompt baseline: same model + same files, without the fitz-forge harness."""
    ctx_path = Path(context_file)
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    file_list: list[str] = ctx.get("file_list") or []
    if not file_list:
        raise typer.BadParameter(f"{context_file} has no 'file_list'")

    challenge_root = ctx_path.parent
    if out_root:
        results_root = Path(out_root)
    else:
        results_root = challenge_root / "results"
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = results_root / f"{ts}_no_harness"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Running {runs} no-harness run(s) -> {out_dir}")

    async def _main() -> list[dict]:
        results: list[dict] = []
        for i in range(runs):
            r = await _one_run(
                run_id=i + 1,
                source_dir=Path(source_dir),
                file_list=file_list,
                query=query,
                out_dir=out_dir,
                max_file_bytes=max_file_bytes,
                max_prompt_chars=max_prompt_chars,
                max_tokens=max_tokens,
            )
            results.append(r)
            logger.info(
                f"Run {i + 1}: {'ok' if r['success'] else 'FAIL'} — "
                f"{r.get('artifact_count', 0)} artifact(s), "
                f"{r['elapsed_s']}s"
            )
        return results

    results = asyncio.run(_main())

    (out_dir / "runs.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Summary
    successes = sum(1 for r in results if r["success"])
    artifact_counts = [r.get("artifact_count", 0) for r in results]
    lines = [
        "# No-Harness Baseline",
        "",
        f"- Runs: {len(results)}",
        f"- Successes: {successes}/{len(results)}",
        f"- Artifacts per plan: min={min(artifact_counts or [0])} "
        f"avg={sum(artifact_counts) / max(1, len(artifact_counts)):.1f} "
        f"max={max(artifact_counts or [0])}",
        f"- Mean latency: {sum(r['elapsed_s'] for r in results) / max(1, len(results)):.1f}s",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))

    if score_v2:
        if not taxonomy:
            raise typer.BadParameter("--score-v2 requires --taxonomy")
        if successes == 0:
            logger.warning("No successful runs — skipping scoring")
            return
        from .plan_factory import _prepare_scoring_v2

        # Reuse the same structural index the harness runs see — it's the
        # AST-extracted "synthesized" overview baked into ideal_context.
        # Keeps the scorer's view of the codebase identical across both
        # conditions of the A/B.
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
