# benchmarks/test_f1_dedup.py
"""F1 harness: generate N decompositions, measure duplicate decision rate."""

import argparse
import asyncio
import json
import logging
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.stderr.write("")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("f1_dedup")

QUERY = (
    "Add query result streaming so answers are delivered token-by-token "
    "instead of waiting for the full response"
)
TEMPERATURE = 0.3
DUP_THRESHOLD = 0.85


def find_duplicates(decisions: list[dict], threshold: float = DUP_THRESHOLD):
    """Find duplicate decision pairs by question similarity."""
    questions = [(d.get("id", f"d{i}"), d.get("question", "")) for i, d in enumerate(decisions)]
    dupes = []
    for i in range(len(questions)):
        for j in range(i + 1, len(questions)):
            sim = SequenceMatcher(None, questions[i][1].lower(), questions[j][1].lower()).ratio()
            if sim >= threshold:
                dupes.append((questions[i][0], questions[j][0], sim))
    return dupes


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()

    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.planning.agent import AgentContextGatherer
    from fitz_forge.planning.pipeline.stages.decision_decomposition import (
        DecisionDecompositionStage,
    )

    context_file = Path("benchmarks/ideal_context.json")
    context = json.loads(context_file.read_text())
    source_dir = "../fitz-sage"

    config = load_config()
    client = create_llm_client(config)

    if hasattr(client, "health_check"):
        await client.health_check()

    # Agent gathering
    agent = AgentContextGatherer(config=config.agent, source_dir=source_dir)
    agent_result = await agent.gather(
        client=client,
        job_description=QUERY,
        override_files=context.get("file_list"),
    )

    prior_outputs = {
        "_gathered_context": agent_result.get("synthesized", ""),
        "_raw_summaries": agent_result.get("raw_summaries", ""),
        "_file_contents": agent_result.get("file_contents", {}),
        "_full_structural_index": agent_result.get("full_structural_index", ""),
    }

    stage = DecisionDecompositionStage()
    messages = stage.build_prompt(QUERY, prior_outputs)

    print(f"\n{'='*80}")
    print(f"F1 Duplicate Detection — {args.runs} runs at temperature={TEMPERATURE}")
    print(f"Similarity threshold: {DUP_THRESHOLD}")
    print(f"{'='*80}\n")

    has_dupes_count = 0
    total_dupes = 0
    results = []

    for i in range(args.runs):
        t0 = time.monotonic()
        try:
            raw = await client.generate(
                messages=messages, temperature=TEMPERATURE, max_tokens=16384,
            )
            elapsed = time.monotonic() - t0
            parsed = stage.parse_output(raw)
            decisions = parsed.get("decisions", [])
            dupes = find_duplicates(decisions)

            if dupes:
                has_dupes_count += 1
                total_dupes += len(dupes)

            status = f"DUPES({len(dupes)})" if dupes else "clean"
            print(f"[{i+1:2d}/{args.runs}] {len(decisions):2d} decisions | "
                  f"{status:10s} | {elapsed:.1f}s", end="")
            if dupes:
                for d1, d2, sim in dupes[:3]:
                    print(f"  {d1}={d2}({sim:.2f})", end="")
            print()

            results.append({
                "run": i + 1,
                "n_decisions": len(decisions),
                "n_dupe_pairs": len(dupes),
                "dupes": [(d1, d2, round(sim, 3)) for d1, d2, sim in dupes],
                "elapsed": elapsed,
            })

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"[{i+1:2d}/{args.runs}] FAILED ({elapsed:.1f}s): {e}")
            results.append({"run": i + 1, "error": str(e)})

    # Summary
    valid = [r for r in results if "error" not in r]
    print(f"\n{'='*80}")
    print(f"RESULTS: {has_dupes_count}/{len(valid)} runs had duplicates "
          f"({has_dupes_count/len(valid)*100:.0f}%)" if valid else "No valid runs")
    print(f"Total duplicate pairs found: {total_dupes}")
    if valid:
        decision_counts = [r["n_decisions"] for r in valid]
        print(f"Decision counts: min={min(decision_counts)} max={max(decision_counts)} "
              f"avg={sum(decision_counts)/len(decision_counts):.1f}")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
