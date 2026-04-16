# benchmarks/plan_factory.py
"""
Benchmark factory for rapid retrieval and reasoning evaluation.

Retrieval benchmark:
    python -m benchmarks.plan_factory retrieval --runs 10 --source-dir ../fitz-sage

Reasoning benchmark (uses pre-gathered "perfect" context):
    python -m benchmarks.plan_factory reasoning --runs 3 --source-dir ../fitz-sage --context-file benchmarks/ideal_context.json

Both write results to benchmarks/results/<timestamp>/.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import typer

sys.stderr.write("")  # force stderr init before logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("bench")

app = typer.Typer(no_args_is_help=True)


class _NullCheckpointManager:
    """No-op checkpoint manager for benchmarks (no persistence needed)."""

    async def save_stage(self, job_id: str, stage_name: str, output: dict) -> None:
        pass

    async def load_checkpoint(self, job_id: str) -> dict | None:
        return None

    async def clear_checkpoint(self, job_id: str) -> None:
        pass


class _SnapshotCheckpointManager:
    """Checkpoint manager that returns a pre-loaded snapshot for replay."""

    def __init__(self, snapshot: dict):
        self._snapshot = snapshot

    async def save_stage(self, job_id: str, stage_name: str, output: dict) -> None:
        pass

    async def load_checkpoint(self, job_id: str) -> dict:
        return dict(self._snapshot)

    async def clear_checkpoint(self, job_id: str) -> None:
        pass


def _ts() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def _challenge_from_context_file(context_file: str | Path | None) -> str | None:
    """Extract the challenge name from a context file path.

    Returns the challenge name if the path is under
    ``benchmarks/challenges/<name>/``, else None.
    """
    if not context_file:
        return None
    parts = Path(context_file).resolve().parts
    try:
        idx = parts.index("challenges")
    except ValueError:
        return None
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return None


def _all_run_dirs() -> list[Path]:
    """Every existing run directory across all challenges + legacy results/."""
    root = Path(__file__).parent
    candidates: list[Path] = []
    challenges_root = root / "challenges"
    if challenges_root.is_dir():
        for ch in challenges_root.iterdir():
            rdir = ch / "results"
            if rdir.is_dir():
                candidates.extend(d for d in rdir.iterdir() if d.is_dir())
    legacy = root / "results"
    if legacy.is_dir():
        candidates.extend(d for d in legacy.iterdir() if d.is_dir())
    return candidates


def _next_run_number() -> int:
    """Find the next run number across all challenge result directories."""
    max_num = 0
    for d in _all_run_dirs():
        name = d.name
        if "_run_" in name:
            try:
                num = int(name.rsplit("_run_", 1)[1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                pass
    return max_num + 1


def _results_dir(label: str, context_file: str | Path | None = None) -> Path:
    """Create a new results directory, routed by challenge when possible."""
    challenge = _challenge_from_context_file(context_file)
    run_num = _next_run_number()
    root = Path(__file__).parent
    if challenge:
        d = root / "challenges" / challenge / "results" / f"{_ts()}_run_{run_num:03d}"
    else:
        d = root / "results" / f"{_ts()}_run_{run_num:03d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------
# Retrieval benchmark
# ------------------------------------------------------------------


async def _run_retrieval_once(
    source_dir: str,
    query: str,
    run_id: int,
) -> dict:
    """Run a single retrieval and return metadata."""
    from fitz_sage.code import CodeRetriever

    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.planning.agent.gatherer import (
        _make_chat_factory,
    )

    config = load_config()
    client = create_llm_client(config)

    # Health check — ensure model is loaded
    if hasattr(client, "health_check"):
        await client.health_check()

    # Switch to smart_model if different
    if (
        hasattr(client, "switch_model")
        and hasattr(client, "smart_model")
        and client.smart_model != client.model
    ):
        loaded = await client.get_loaded_model() if hasattr(client, "get_loaded_model") else None
        if loaded != client.smart_model:
            await client.switch_model(client.smart_model)

    loop = asyncio.get_running_loop()
    chat_factory = _make_chat_factory(client, loop)

    retriever = CodeRetriever(
        source_dir=source_dir,
        chat_factory=chat_factory,
        llm_tier="smart",
        max_file_bytes=config.agent.max_file_bytes,
    )

    t0 = time.monotonic()
    results = await asyncio.to_thread(retriever.retrieve, query)
    elapsed = time.monotonic() - t0

    # Extract provenance
    scan_hits = []
    import_added = []
    neighbor_added = []
    all_files = []

    for r in results:
        origin = r.address.metadata.get("origin", "neighbor")
        all_files.append(r.file_path)
        if origin == "selected":
            scan_hits.append(r.file_path)
        elif origin == "import":
            import_added.append(r.file_path)
        elif origin == "neighbor":
            neighbor_added.append(r.file_path)

    return {
        "run": run_id,
        "elapsed_s": round(elapsed, 1),
        "total_files": len(all_files),
        "scan_hits": scan_hits,
        "import_added": import_added,
        "neighbor_added": neighbor_added,
        "all_files": all_files,
    }


@app.command()
def retrieval(
    runs: int = typer.Option(10, help="Number of retrieval runs"),
    source_dir: str = typer.Option(..., help="Codebase to index"),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Job description / query",
    ),
):
    """Run retrieval-only benchmarks (no planning stages)."""
    out_dir = _results_dir("retrieval")
    logger.info(f"Running {runs} retrieval benchmarks -> {out_dir}")

    all_results = []

    async def _run_all():
        for i in range(runs):
            logger.info(f"--- Retrieval run {i + 1}/{runs} ---")
            result = await _run_retrieval_once(source_dir, query, i + 1)
            all_results.append(result)

            # Save each run
            run_file = out_dir / f"run_{i + 1:02d}.json"
            run_file.write_text(json.dumps(result, indent=2))

            scan = result["scan_hits"]
            logger.info(
                f"Run {i + 1}: {len(scan)} scan hits, "
                f"{result['total_files']} total, {result['elapsed_s']}s"
            )

    asyncio.run(_run_all())

    # Summary
    _print_retrieval_summary(all_results, out_dir)


def _print_retrieval_summary(results: list[dict], out_dir: Path) -> None:
    """Print and save retrieval benchmark summary."""
    lines = []
    lines.append(f"# Retrieval Benchmark ({len(results)} runs)\n")

    # Timing
    times = [r["elapsed_s"] for r in results]
    lines.append("## Timing")
    lines.append(f"- Min: {min(times):.1f}s")
    lines.append(f"- Max: {max(times):.1f}s")
    lines.append(f"- Avg: {sum(times) / len(times):.1f}s\n")

    # File count consistency
    totals = [r["total_files"] for r in results]
    scans = [len(r["scan_hits"]) for r in results]
    lines.append("## File Counts")
    lines.append(f"- Total files: {min(totals)}-{max(totals)}")
    lines.append(f"- Scan hits: {min(scans)}-{max(scans)}\n")

    # Scan hit frequency
    hit_freq: dict[str, int] = {}
    for r in results:
        for f in r["scan_hits"]:
            hit_freq[f] = hit_freq.get(f, 0) + 1

    lines.append(f"## Scan Hit Frequency (across {len(results)} runs)")
    lines.append("| File | Hits | % |")
    lines.append("|------|------|---|")
    for path, count in sorted(hit_freq.items(), key=lambda x: -x[1]):
        pct = 100 * count / len(results)
        lines.append(f"| {path} | {count}/{len(results)} | {pct:.0f}% |")

    # All-files frequency
    all_freq: dict[str, int] = {}
    for r in results:
        for f in r["all_files"]:
            all_freq[f] = all_freq.get(f, 0) + 1

    lines.append("\n## All Selected Files Frequency")
    lines.append("| File | Hits | % | Signal |")
    lines.append("|------|------|---|--------|")
    # Determine most common signal per file
    signal_map: dict[str, str] = {}
    for r in results:
        for f in r["scan_hits"]:
            signal_map[f] = "scan"
        for f in r["import_added"]:
            if f not in signal_map:
                signal_map[f] = "import"
        for f in r["neighbor_added"]:
            if f not in signal_map:
                signal_map[f] = "neighbor"

    for path, count in sorted(all_freq.items(), key=lambda x: -x[1]):
        pct = 100 * count / len(results)
        sig = signal_map.get(path, "?")
        lines.append(f"| {path} | {count}/{len(results)} | {pct:.0f}% | {sig} |")

    # Critical file check
    critical_files = [
        "fitz_sage/engines/fitz_krag/engine.py",
        "fitz_sage/core/answer.py",
        "fitz_sage/engines/fitz_krag/query_analyzer.py",
        "fitz_sage/retrieval/detection/registry.py",
    ]
    lines.append("\n## Critical File Discovery")
    lines.append("| File | Found | % |")
    lines.append("|------|-------|---|")
    for cf in critical_files:
        found = all_freq.get(cf, 0)
        pct = 100 * found / len(results)
        lines.append(f"| {cf} | {found}/{len(results)} | {pct:.0f}% |")

    summary = "\n".join(lines)
    (out_dir / "SUMMARY.md").write_text(summary)
    print(summary)


# ------------------------------------------------------------------
# Reasoning benchmark
# ------------------------------------------------------------------


async def _run_reasoning_once(
    source_dir: str,
    query: str,
    context: dict,
    run_id: int,
    out_dir: Path,
    *,
    split_reasoning: bool = False,
    max_seed_files: int | None = None,
) -> dict:
    """Run the real planning pipeline with fixed retrieval files.

    Uses AgentContextGatherer with override_files to skip LLM retrieval
    but run identical post-processing (compress, structural overview,
    seed splitting, tool pool, provenance). The planning stages then
    execute exactly as they would in a normal run.
    """
    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.planning.agent import AgentContextGatherer
    from fitz_forge.planning.pipeline.orchestrator import PlanningPipeline
    from fitz_forge.planning.pipeline.stages import ContextStage, RoadmapRiskStage
    from fitz_forge.planning.pipeline.stages.architecture_design import ArchitectureDesignStage

    config = load_config()
    client = create_llm_client(config)

    # Health check
    if hasattr(client, "health_check"):
        await client.health_check()

    # Ensure planning model is loaded (not the agent model)
    if hasattr(client, "switch_model"):
        loaded = await client.get_loaded_model() if hasattr(client, "get_loaded_model") else None
        if loaded != client.model:
            await client.switch_model(client.model)

    stages = [
        ContextStage(),
        ArchitectureDesignStage(split_reasoning=split_reasoning),
        RoadmapRiskStage(split_reasoning=split_reasoning),
    ]
    pipeline = PlanningPipeline(
        stages=stages,
        checkpoint_manager=_NullCheckpointManager(),
    )
    job_id = f"bench_{run_id:03d}"

    # Override max_seed_files if requested
    if max_seed_files is not None:
        config.agent.max_seed_files = max_seed_files

    # Create agent with override_files — skips LLM retrieval,
    # runs identical post-processing as the real pipeline
    agent = AgentContextGatherer(
        config=config.agent,
        source_dir=source_dir,
    )

    t0 = time.monotonic()
    result = await pipeline.execute(
        client=client,
        job_id=job_id,
        job_description=query,
        resume=False,
        agent=agent,
        _bench_override_files=context.get("file_list"),
    )
    elapsed = time.monotonic() - t0

    # Save plan outputs if successful
    plan_text = ""
    if result.success:
        # Save raw outputs as JSON (avoids PlanOutput/PlanRenderer coupling)
        plan_data = {k: v for k, v in result.outputs.items() if not k.startswith("_")}
        plan_text = json.dumps(plan_data, indent=2, default=str)
        plan_file = out_dir / f"plan_{run_id:02d}.json"
        plan_file.write_text(plan_text)

    # Extract architecture decision
    arch = result.outputs.get("architecture", {})
    recommended = arch.get("recommended", "")

    return {
        "run": run_id,
        "elapsed_s": round(elapsed, 1),
        "success": result.success,
        "recommended": recommended,
        "plan_size": len(plan_text),
        "stage_timings": result.stage_timings,
        "error": result.error,
    }


@app.command()
def reasoning(
    runs: int = typer.Option(3, help="Number of reasoning runs"),
    source_dir: str = typer.Option(..., help="Codebase source dir (for file reads)"),
    context_file: str = typer.Option(..., help="JSON file with pre-gathered context"),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Job description / query",
    ),
    split: bool = typer.Option(False, help="Split arch+design into two reasoning calls"),
    max_seeds: int = typer.Option(None, help="Override max_seed_files (default: config value)"),
):
    """Run reasoning-only benchmarks with fixed retrieval context."""
    context = json.loads(Path(context_file).read_text())
    label = "reasoning"
    if split:
        label += "_split"
    if max_seeds is not None:
        label += f"_seeds{max_seeds}"
    out_dir = _results_dir(label)
    logger.info(f"Running {runs} reasoning benchmarks -> {out_dir}")
    if split:
        logger.info("Split reasoning mode: architecture + design as separate calls")
    if max_seeds is not None:
        logger.info(f"Max seed files: {max_seeds}")

    all_results = []

    async def _run_all():
        for i in range(runs):
            logger.info(f"--- Reasoning run {i + 1}/{runs} ---")
            result = await _run_reasoning_once(
                source_dir,
                query,
                context,
                i + 1,
                out_dir,
                split_reasoning=split,
                max_seed_files=max_seeds,
            )
            all_results.append(result)

            run_file = out_dir / f"run_{i + 1:02d}.json"
            run_file.write_text(json.dumps(result, indent=2))

            logger.info(
                f"Run {i + 1}: {result['recommended']} "
                f"({result['elapsed_s']}s, success={result['success']})"
            )

    asyncio.run(_run_all())

    _print_reasoning_summary(all_results, out_dir)


def _print_reasoning_summary(results: list[dict], out_dir: Path) -> None:
    """Print and save reasoning benchmark summary."""
    lines = []
    lines.append(f"# Reasoning Benchmark ({len(results)} runs)\n")

    # Timing
    times = [r["elapsed_s"] for r in results if r["success"]]
    if times:
        lines.append("## Timing")
        lines.append(f"- Min: {min(times):.0f}s")
        lines.append(f"- Max: {max(times):.0f}s")
        lines.append(f"- Avg: {sum(times) / len(times):.0f}s\n")

    # Success rate
    successes = sum(1 for r in results if r["success"])
    lines.append(f"## Success: {successes}/{len(results)}\n")

    # Architecture decisions
    lines.append("## Architecture Decisions")
    lines.append("| Run | Recommended | Time | Size |")
    lines.append("|-----|-------------|------|------|")
    for r in results:
        lines.append(f"| {r['run']} | {r['recommended']} | {r['elapsed_s']}s | {r['plan_size']}B |")

    # Decision frequency
    decisions: dict[str, int] = {}
    for r in results:
        if r["success"]:
            decisions[r["recommended"]] = decisions.get(r["recommended"], 0) + 1

    lines.append("\n## Decision Frequency")
    lines.append("| Approach | Count | % |")
    lines.append("|----------|-------|---|")
    for approach, count in sorted(decisions.items(), key=lambda x: -x[1]):
        pct = 100 * count / len(results)
        lines.append(f"| {approach} | {count} | {pct:.0f}% |")

    # Stage timings (average)
    stage_keys = set()
    for r in results:
        if r.get("stage_timings"):
            stage_keys.update(r["stage_timings"].keys())

    if stage_keys:
        lines.append("\n## Avg Stage Timings")
        lines.append("| Stage | Avg | Min | Max |")
        lines.append("|-------|-----|-----|-----|")
        for key in sorted(stage_keys):
            vals = [
                r["stage_timings"][key]
                for r in results
                if r.get("stage_timings") and key in r["stage_timings"]
            ]
            if vals:
                lines.append(
                    f"| {key} | {sum(vals) / len(vals):.0f}s | {min(vals):.0f}s | {max(vals):.0f}s |"
                )

    summary = "\n".join(lines)
    (out_dir / "SUMMARY.md").write_text(summary)
    print(summary)


async def _run_decomposed_once(
    source_dir: str,
    query: str,
    context: dict,
    run_id: int,
    out_dir: Path,
) -> dict:
    """Run the decomposed planning pipeline with fixed retrieval files."""
    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.llm.generate import configure_tracing
    from fitz_forge.planning.agent import AgentContextGatherer
    from fitz_forge.planning.pipeline.orchestrator import DecomposedPipeline

    config = load_config()
    client = create_llm_client(config)

    if hasattr(client, "health_check"):
        await client.health_check()

    pipeline = DecomposedPipeline(
        checkpoint_manager=_NullCheckpointManager(),
    )
    # Enable full LLM call provenance tracing
    trace_dir = out_dir / f"traces_{run_id:02d}"
    configure_tracing(trace_dir)
    # Also set per-stage trace_dir for legacy prompt/response text tracing
    for stage in pipeline._stages:
        if hasattr(stage, "trace_dir"):
            stage.trace_dir = str(trace_dir)

    job_id = f"decomp_{run_id:03d}"

    agent = AgentContextGatherer(
        config=config.agent,
        source_dir=source_dir,
    )

    t0 = time.monotonic()
    result = await pipeline.execute(
        client=client,
        job_id=job_id,
        job_description=query,
        resume=False,
        agent=agent,
        _bench_override_files=context.get("file_list"),
    )
    elapsed = time.monotonic() - t0
    configure_tracing(None)  # disable tracing between runs

    plan_text = ""
    if result.success:
        plan_data = {k: v for k, v in result.outputs.items() if not k.startswith("_")}
        plan_text = json.dumps(plan_data, indent=2, default=str)
        plan_file = out_dir / f"plan_{run_id:02d}.json"
        plan_file.write_text(plan_text)

    arch = result.outputs.get("architecture", {})
    recommended = arch.get("recommended", "")

    return {
        "run": run_id,
        "elapsed_s": round(elapsed, 1),
        "success": result.success,
        "recommended": recommended,
        "plan_size": len(plan_text),
        "stage_timings": result.stage_timings,
        "error": result.error,
        "num_decisions": len(result.outputs.get("decision_decomposition", {}).get("decisions", [])),
    }


@app.command()
def decomposed(
    runs: int = typer.Option(3, help="Number of decomposed runs"),
    source_dir: str = typer.Option(..., help="Codebase source dir"),
    context_file: str = typer.Option(..., help="JSON file with pre-gathered context"),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Job description / query",
    ),
    score_plans: bool = typer.Option(
        False,
        "--score",
        help="Prepare scoring prompts after generation",
    ),
    score_v2: bool = typer.Option(
        False,
        "--score-v2",
        help="Run Scorer V2 (deterministic + taxonomy prompts)",
    ),
    parallel_runs: int = typer.Option(
        1,
        "--parallel-runs",
        "-p",
        help="Run N plans concurrently (requires LM Studio --parallel N)",
    ),
    taxonomy: str = typer.Option(
        "",
        "--taxonomy",
        help="Path to taxonomy JSON (default: challenges/streaming_implementation/taxonomy.json)",
    ),
):
    """Run decomposed pipeline benchmarks with fixed retrieval context."""
    context = json.loads(Path(context_file).read_text())
    out_dir = _results_dir("decomposed", context_file=context_file)
    logger.info(f"Running {runs} decomposed benchmarks -> {out_dir}")
    if parallel_runs > 1:
        logger.info(
            f"Parallel runs: {parallel_runs} (ensure LM Studio loaded with --parallel {parallel_runs})"
        )

    all_results = []

    async def _run_one(i: int) -> dict:
        logger.info(f"--- Decomposed run {i + 1}/{runs} ---")
        result = await _run_decomposed_once(
            source_dir,
            query,
            context,
            i + 1,
            out_dir,
        )
        run_file = out_dir / f"run_{i + 1:02d}.json"
        run_file.write_text(json.dumps(result, indent=2))
        logger.info(
            f"Run {i + 1}: {result['recommended']} "
            f"({result['elapsed_s']}s, {result['num_decisions']} decisions, "
            f"success={result['success']})"
        )
        return result

    async def _run_all():
        for batch_start in range(0, runs, parallel_runs):
            batch_end = min(batch_start + parallel_runs, runs)
            batch_indices = list(range(batch_start, batch_end))
            if len(batch_indices) > 1:
                logger.info(f"Starting batch: runs {[i + 1 for i in batch_indices]}")
            results = await asyncio.gather(*[_run_one(i) for i in batch_indices])
            all_results.extend(results)

    asyncio.run(_run_all())
    _print_reasoning_summary(all_results, out_dir)

    if score_plans:
        structural_index = context.get("synthesized", "")
        _prepare_scoring(str(out_dir), source_dir, query, structural_index)

    if score_v2:
        structural_index = context.get("synthesized", "")
        _prepare_scoring_v2(str(out_dir), query, structural_index, source_dir, taxonomy_file=taxonomy)


# ------------------------------------------------------------------
# Sonnet-as-Judge scoring (prompt preparation)
# ------------------------------------------------------------------


def _prepare_scoring(
    results_dir: str,
    source_dir: str,
    query: str,
    structural_index: str,
) -> None:
    """Prepare scoring prompts for Claude Code evaluation."""
    from .eval_plans import prepare_batch

    plan_dir = Path(results_dir)
    prompts = prepare_batch(
        plan_dir,
        query,
        structural_index,
        Path(source_dir),
    )
    logger.info(
        f"Wrote {len(prompts)} scoring prompts to {plan_dir}. Score them via Claude Code subagents."
    )


@app.command("prepare-scoring")
def prepare_scoring(
    results_dir: str = typer.Option(..., help="Directory containing plan_*.json files"),
    source_dir: str = typer.Option(..., help="Target codebase directory"),
    context_file: str = typer.Option(..., help="ideal_context.json for structural index"),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="The task query these plans address",
    ),
):
    """Prepare scoring prompts for Claude Code evaluation.

    Writes score_prompt_NN.md files alongside the plans. Feed these
    to Claude Code subagents or read them in conversation to score.
    """
    context = json.loads(Path(context_file).read_text())
    structural_index = context.get("synthesized", "")
    if not structural_index:
        logger.error("No 'synthesized' field in context file")
        raise typer.Exit(1)

    _prepare_scoring(results_dir, source_dir, query, structural_index)


# ------------------------------------------------------------------
# Scorer V2 (deterministic + taxonomy)
# ------------------------------------------------------------------


def _prepare_scoring_v2(
    results_dir: str,
    query: str,
    structural_index: str,
    source_dir: str = "",
    taxonomy_file: str = "",
) -> None:
    """Run Scorer V2: deterministic checks + taxonomy prompts."""
    from .eval_v2 import _find_plan_files, format_batch_report, score_batch_deterministic
    from .eval_v2_deterministic import run_deterministic_checks
    from .eval_v2_taxonomy import build_taxonomy_prompt, load_taxonomy

    plan_dir = Path(results_dir)
    taxonomy_path = Path(taxonomy_file) if taxonomy_file else Path(__file__).parent / "challenges" / "streaming_implementation" / "taxonomy.json"

    tax_files = None
    if taxonomy_path.exists():
        taxonomy_def = load_taxonomy(taxonomy_path)
        tax_files = taxonomy_def.required_files or None

    # Run deterministic scoring
    batch = score_batch_deterministic(plan_dir, structural_index, query, tax_files, source_dir)

    # Generate taxonomy prompts
    if taxonomy_path.exists():
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

    # Write reports
    report = format_batch_report(batch)
    (plan_dir / "SCORE_V2_SUMMARY.md").write_text(report, encoding="utf-8")
    (plan_dir / "scores_v2.json").write_text(
        json.dumps(batch.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(f"Scorer V2: {batch.plans_scored} plans, avg {batch.deterministic_average}/100")


@app.command("prepare-scoring-v2")
def prepare_scoring_v2(
    results_dir: str = typer.Option(..., help="Directory containing plan_*.json files"),
    context_file: str = typer.Option(..., help="ideal_context.json for structural index"),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="The task query these plans address",
    ),
):
    """Run Scorer V2 on existing plans.

    Runs deterministic checks instantly and writes taxonomy prompts
    for Sonnet classification.
    """
    context = json.loads(Path(context_file).read_text())
    structural_index = context.get("synthesized", "")
    if not structural_index:
        logger.error("No 'synthesized' field in context file")
        raise typer.Exit(1)

    _prepare_scoring_v2(results_dir, query, structural_index)


async def _run_replay_once(
    source_dir: str,
    query: str,
    context: dict,
    snapshot_path: Path,
    out_dir: Path,
) -> dict:
    """Replay a pipeline run from a saved snapshot.

    Loads prior_outputs from the snapshot, injects them into a fresh
    pipeline, and runs only the remaining stages with the real LLM.
    """
    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.llm.generate import configure_tracing
    from fitz_forge.planning.agent import AgentContextGatherer
    from fitz_forge.planning.pipeline.orchestrator import DecomposedPipeline

    config = load_config()
    client = create_llm_client(config)

    if hasattr(client, "health_check"):
        await client.health_check()

    # Load snapshot as prior_outputs
    prior_outputs = json.loads(snapshot_path.read_text(encoding="utf-8"))

    # Reconstruct _source_dir (stripped from snapshot)
    prior_outputs["_source_dir"] = source_dir

    # Reconstruct _file_contents from override files
    file_list = context.get("file_list")
    if file_list:
        file_contents = {}
        for rel_path in file_list:
            full = Path(source_dir) / rel_path
            if full.exists():
                try:
                    file_contents[rel_path] = full.read_text(encoding="utf-8")
                except Exception:
                    pass
        if file_contents:
            prior_outputs["_file_contents"] = file_contents

    # Reconstruct call graph (non-serializable object stripped from snapshot)
    if "_call_graph" not in prior_outputs and "_call_graph_text" in prior_outputs:
        from fitz_forge.planning.agent.indexer import build_import_graph
        from fitz_forge.planning.pipeline.call_graph import extract_call_graph

        agent_ctx = prior_outputs.get("_agent_context", {})
        agent_files = agent_ctx.get("agent_files", {})
        all_files = agent_files.get("all_files", []) or agent_files.get("included", [])
        if source_dir and all_files:
            forward_map_raw, _ = build_import_graph(source_dir, all_files)
            forward_map = dict(forward_map_raw)
        else:
            forward_map = {}

        structural_index = prior_outputs.get("_gathered_context", "")
        included = agent_files.get("included", [])

        call_graph = extract_call_graph(
            task_description=query,
            structural_index=structural_index,
            forward_map=forward_map,
            file_index_entries={},
            max_depth=3,
            seed_files=included,
            source_dir=source_dir,
        )
        prior_outputs["_call_graph"] = call_graph
        prior_outputs["_call_graph_text"] = call_graph.format_for_prompt()

    # Figure out which stages are already done
    completed = {k for k in prior_outputs if not k.startswith("_")}
    all_stages = ["decision_decomposition", "decision_resolution", "synthesis"]
    remaining = [s for s in all_stages if s not in completed]
    logger.info(
        f"Replay: loaded snapshot from {snapshot_path.name}, "
        f"completed={sorted(completed)}, remaining={remaining}"
    )

    # Use snapshot checkpoint manager so resume=True loads our prior_outputs
    pipeline = DecomposedPipeline(
        checkpoint_manager=_SnapshotCheckpointManager(prior_outputs),
    )

    # Enable tracing for the replay run
    trace_dir = out_dir / "traces_replay"
    configure_tracing(trace_dir)
    for stage in pipeline._stages:
        if hasattr(stage, "trace_dir"):
            stage.trace_dir = str(trace_dir)

    agent = AgentContextGatherer(
        config=config.agent,
        source_dir=source_dir,
    )

    t0 = time.monotonic()
    result = await pipeline.execute(
        client=client,
        job_id="replay",
        job_description=query,
        resume=True,
        agent=agent,
        _bench_override_files=context.get("file_list"),
    )
    elapsed = time.monotonic() - t0
    configure_tracing(None)

    plan_text = ""
    if result.success:
        plan_data = {k: v for k, v in result.outputs.items() if not k.startswith("_")}
        plan_text = json.dumps(plan_data, indent=2, default=str)
        plan_file = out_dir / "plan_replay.json"
        plan_file.write_text(plan_text)
        logger.info(f"Replay: plan written to {plan_file}")

    arch = result.outputs.get("architecture", {})
    recommended = arch.get("recommended", "")

    return {
        "run": "replay",
        "elapsed_s": round(elapsed, 1),
        "recommended": recommended,
        "success": result.success,
        "error": result.error if not result.success else None,
        "plan_size": len(plan_text),
        "replayed_from": snapshot_path.name,
        "stages_skipped": sorted(completed),
        "stages_run": remaining,
    }


@app.command("replay")
def replay_cmd(
    snapshot: str = typer.Option(
        ...,
        help="Path to snapshot JSON (e.g. traces_03/snapshot_after_decision_decomposition.json)",
    ),
    source_dir: str = typer.Option(..., "--source-dir", help="Target codebase"),
    context_file: str = typer.Option(..., help="ideal_context.json"),
    query: str = typer.Option(
        "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response",
        help="Task query",
    ),
    score_v2: bool = typer.Option(False, "--score-v2", help="Run V2 scorer after replay"),
):
    """Replay a pipeline run from a saved stage snapshot.

    Loads the snapshot (prior_outputs from a previous run), skips the
    completed stages, and re-runs only the remaining stages with the
    real LLM. Use this to test pipeline changes without re-running
    the full 10-minute pipeline.

    Example:
        python -m benchmarks.plan_factory replay \\
          --snapshot benchmarks/results/.../traces_03/snapshot_after_decision_decomposition.json \\
          --source-dir ../fitz-sage \\
          --context-file benchmarks/ideal_context.json
    """
    snapshot_path = Path(snapshot)
    if not snapshot_path.exists():
        logger.error(f"Snapshot not found: {snapshot_path}")
        raise typer.Exit(1)

    context = json.loads(Path(context_file).read_text())
    out_dir = snapshot_path.parent.parent  # traces_XX is inside the results dir

    result = asyncio.run(_run_replay_once(source_dir, query, context, snapshot_path, out_dir))

    print(json.dumps(result, indent=2))

    if score_v2 and result.get("success"):
        structural_index = context.get("synthesized", "")
        _prepare_scoring_v2(str(out_dir), query, structural_index, source_dir)

        # Also score plan_replay.json specifically — _find_plan_files skips
        # it, so without this the replay output is invisible to the scorer
        # and every cycle reports the unchanged original average.
        replay_path = out_dir / "plan_replay.json"
        if replay_path.exists():
            from .eval_v2_deterministic import run_deterministic_checks
            from .eval_v2_taxonomy import load_taxonomy

            taxonomy_path = Path(__file__).parent / "challenges" / "streaming_implementation" / "taxonomy.json"
            tax_files = None
            if taxonomy_path.exists():
                tax_files = load_taxonomy(taxonomy_path).required_files or None
            replay_data = json.loads(replay_path.read_text(encoding="utf-8"))
            rep = run_deterministic_checks(
                replay_data,
                structural_index,
                task_requires_streaming=True,
                taxonomy_files=tax_files,
                source_dir=source_dir,
            )
            logger.info(
                f"Replay score: total={rep.deterministic_score} "
                f"(comp={rep.completeness_score} "
                f"art={rep.artifact_quality_score} "
                f"cons={rep.consistency_score})"
            )
            print(
                f"\n=== REPLAY DETERMINISTIC SCORE ===\n"
                f"  total: {rep.deterministic_score}/100\n"
                f"  completeness: {rep.completeness_score}/30\n"
                f"  artifacts:    {rep.artifact_quality_score}/50\n"
                f"  consistency:  {rep.consistency_score}/20\n"
            )


if __name__ == "__main__":
    app()
