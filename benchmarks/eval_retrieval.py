# benchmarks/eval_retrieval.py
"""
Evaluate retrieval quality against per-challenge ground truth.

Ground truth lives at ``benchmarks/challenges/<name>/retrieval_ground_truth.json``.
The challenge folder name is the query ID.

Usage:
    python -m benchmarks.eval_retrieval --source-dir ../fitz-sage
    python -m benchmarks.eval_retrieval --source-dir ../fitz-sage --challenge streaming_implementation
    python -m benchmarks.eval_retrieval --source-dir ../fitz-sage --challenge streaming_implementation,ranking_explanation
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import typer

sys.stderr.write("")
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("eval")

app = typer.Typer()

CHALLENGES_DIR = Path(__file__).parent / "challenges"


def _load_challenges(selected: set[str] | None) -> list[dict]:
    """Load per-challenge retrieval ground truth files.

    Returns entries of shape:
        {"id": <folder-name>, "query": str, "critical_files": [...],
         "relevant_files": [...] (optional), "codebase": str (optional)}
    """
    entries: list[dict] = []
    for gt_file in sorted(CHALLENGES_DIR.glob("*/retrieval_ground_truth.json")):
        name = gt_file.parent.name
        if selected is not None and name not in selected:
            continue
        data = json.loads(gt_file.read_text())
        entries.append(
            {
                "id": name,
                "query": data["query"],
                "codebase": data.get("codebase"),
                "critical_files": data.get("critical_files", []),
                "relevant_files": data.get("relevant_files", []),
            }
        )
    return entries


async def _run_retrieval(source_dir: str, query: str) -> list[str]:
    """Run retrieval pipeline and return selected file paths."""
    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.planning.agent.gatherer import (
        AgentContextGatherer,
        _make_chat_factory,
    )
    from fitz_sage.code import CodeRetriever

    config = load_config()
    client = create_llm_client(config)

    if hasattr(client, "health_check"):
        await client.health_check()

    if hasattr(client, "switch_model") and hasattr(client, "smart_model"):
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

    results = await asyncio.to_thread(retriever.retrieve, query)
    return [r.file_path for r in results]


def _score(retrieved: list[str], critical: list[str], relevant: list[str]) -> dict:
    """Score retrieval against ground truth."""
    retrieved_set = set(retrieved)
    critical_set = set(critical)
    relevant_set = set(relevant)
    all_ground_truth = critical_set | relevant_set

    critical_found = critical_set & retrieved_set
    relevant_found = relevant_set & retrieved_set
    all_found = all_ground_truth & retrieved_set

    critical_recall = len(critical_found) / len(critical_set) if critical_set else 1.0
    total_recall = len(all_found) / len(all_ground_truth) if all_ground_truth else 1.0
    precision = len(all_found) / len(retrieved_set) if retrieved_set else 0.0

    return {
        "critical_recall": round(critical_recall, 2),
        "critical_found": sorted(critical_found),
        "critical_missed": sorted(critical_set - retrieved_set),
        "total_recall": round(total_recall, 2),
        "relevant_found": sorted(relevant_found),
        "relevant_missed": sorted(relevant_set - retrieved_set),
        "precision": round(precision, 2),
        "retrieved_count": len(retrieved),
    }


@app.command()
def run(
    source_dir: str = typer.Option(..., help="Codebase root directory"),
    challenge: str = typer.Option(
        None,
        help="Comma-separated challenge folder names to run (default: all)",
    ),
    verbose: bool = typer.Option(False, "-v", help="Show per-query details"),
):
    """Evaluate retrieval pipeline against ground truth."""
    selected = (
        {c.strip() for c in challenge.split(",") if c.strip()} if challenge else None
    )
    queries = _load_challenges(selected)

    if not queries:
        if selected:
            print(f"No challenges found matching: {sorted(selected)}")
            available = sorted(p.parent.name for p in CHALLENGES_DIR.glob("*/retrieval_ground_truth.json"))
            print(f"Available: {available}")
        else:
            print(f"No challenges with retrieval_ground_truth.json under {CHALLENGES_DIR}")
        raise typer.Exit(1)

    print(f"Running {len(queries)} retrieval evaluations...\n")

    async def _run_all(results_by_challenge: dict[str, dict]):
        for q in queries:
            t0 = time.monotonic()
            try:
                retrieved = await _run_retrieval(source_dir, q["query"])
            except Exception as e:
                print(f"  [{q['id']}] FAILED: {e}")
                results_by_challenge[q["id"]] = {"id": q["id"], "error": str(e)}
                continue
            elapsed = time.monotonic() - t0

            score = _score(retrieved, q["critical_files"], q.get("relevant_files", []))
            score["id"] = q["id"]
            score["query"] = q["query"][:60]
            score["elapsed_s"] = round(elapsed, 1)
            results_by_challenge[q["id"]] = score

            status = "PASS" if score["critical_recall"] == 1.0 else "MISS"
            print(
                f"  [{q['id']}] {status} "
                f"crit={score['critical_recall']:.0%} "
                f"total={score['total_recall']:.0%} "
                f"({score['elapsed_s']}s) "
                f"{q['query'][:50]}"
            )
            if verbose and score["critical_missed"]:
                for m in score["critical_missed"]:
                    print(f"       MISSED: {m}")

    results_by_challenge: dict[str, dict] = {}
    asyncio.run(_run_all(results_by_challenge))

    results = list(results_by_challenge.values())
    valid = [r for r in results if "error" not in r]
    if not valid:
        print("\nNo successful runs.")
        return

    avg_critical = sum(r["critical_recall"] for r in valid) / len(valid)
    avg_total = sum(r["total_recall"] for r in valid) / len(valid)
    perfect = sum(1 for r in valid if r["critical_recall"] == 1.0)
    avg_time = sum(r["elapsed_s"] for r in valid) / len(valid)

    print(f"\n{'='*60}")
    print(f"RESULTS ({len(valid)} challenges)")
    print(f"{'='*60}")
    print(f"Critical recall:  {avg_critical:.0%} avg ({perfect}/{len(valid)} perfect)")
    print(f"Total recall:     {avg_total:.0%} avg")
    print(f"Avg time:         {avg_time:.1f}s per query")

    miss_count: dict[str, int] = {}
    for r in valid:
        for f in r.get("critical_missed", []):
            miss_count[f] = miss_count.get(f, 0) + 1
    if miss_count:
        print(f"\nMost-missed critical files:")
        for f, count in sorted(miss_count.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count}x {f}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    for challenge_id, result in results_by_challenge.items():
        out_dir = CHALLENGES_DIR / challenge_id / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"retrieval_eval_{ts}.json"
        out.write_text(json.dumps(result, indent=2))
        print(f"  {challenge_id} -> {out}")


if __name__ == "__main__":
    app()
