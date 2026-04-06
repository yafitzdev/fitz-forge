# benchmarks/replay_artifact.py
"""Replay a traced artifact prompt N times and check for F25 violations.

Usage:
    python -m benchmarks.replay_artifact \
        --prompt benchmarks/results/decomposed_.../traces_01/fitz_sage_api_routes_query.py_prompt.txt \
        --runs 10
"""

import argparse
import ast
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.write("")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("replay_artifact")


def check_wrong_fields(content: str, structural_index: str) -> list[dict]:
    """Check for F25 violations using the same logic as grounding.py."""
    from fitz_forge.planning.validation.grounding import (
        StructuralIndexLookup,
        check_artifact,
    )

    lookup = StructuralIndexLookup(structural_index)
    artifact = {"filename": "replay.py", "content": content}
    violations = check_artifact(artifact, lookup)
    return [
        {
            "symbol": v.symbol,
            "kind": v.kind,
            "detail": v.detail,
            "suggestion": v.suggestion,
            "line": v.line,
        }
        for v in violations
        if v.kind == "wrong_field"
    ]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt", required=True,
        help="Path to a *_prompt.txt trace file",
    )
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--structural-index",
        help="Path to structural index file (default: derived from ideal_context.json)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))

    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.planning.pipeline.stages.base import extract_json

    # Load the prompt
    prompt_text = Path(args.prompt).read_text(encoding="utf-8")
    logger.info(f"Loaded prompt: {len(prompt_text)} chars from {args.prompt}")

    # Build structural index for validation
    if args.structural_index:
        structural_index = Path(args.structural_index).read_text(encoding="utf-8")
    else:
        # Run agent gathering to get the index
        from fitz_forge.planning.agent import AgentContextGatherer

        config = load_config()
        agent = AgentContextGatherer(config=config.agent, source_dir="../fitz-sage")
        context_file = Path("benchmarks/ideal_context.json")
        context = json.loads(context_file.read_text())
        client = create_llm_client(config)
        if hasattr(client, "health_check"):
            await client.health_check()
        agent_result = await agent.gather(
            client=client,
            job_description="(replay)",
            override_files=context.get("file_list"),
        )
        structural_index = agent_result.get("full_structural_index", "")
        logger.info(f"Built structural index: {len(structural_index)} chars")

    # Build messages (same format as SynthesisStage._make_messages)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior software architect. Generate precise, "
                "well-grounded code artifacts."
            ),
        },
        {"role": "user", "content": prompt_text},
    ]

    # Connect to LM Studio
    config = load_config()
    client = create_llm_client(config)
    if hasattr(client, "health_check"):
        await client.health_check()

    print(f"\n{'='*80}")
    print(f"Replay Artifact — {args.runs} runs")
    print(f"Prompt: {args.prompt}")
    print(f"{'='*80}\n")

    violation_count = 0
    results = []

    for i in range(args.runs):
        t0 = time.monotonic()
        try:
            raw = await client.generate(
                messages=messages,
                max_tokens=4096,
            )
            elapsed = time.monotonic() - t0

            data = extract_json(raw)
            content = data.get("content", "")
            if not content:
                print(f"[{i+1:2d}/{args.runs}] EMPTY    | {elapsed:.1f}s")
                results.append({"run": i + 1, "empty": True})
                continue

            violations = check_wrong_fields(content, structural_index)

            if violations:
                violation_count += 1

            status = f"VIOL({len(violations):2d})" if violations else "CLEAN    "
            print(
                f"[{i+1:2d}/{args.runs}] {status} | {len(content):5d} chars | "
                f"{elapsed:.1f}s",
                end="",
            )
            if violations:
                v = violations[0]
                print(f" | {v['symbol']}: {v['detail'][:60]}", end="")
            print()

            results.append({
                "run": i + 1,
                "violations": violations,
                "chars": len(content),
                "elapsed": elapsed,
            })

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"[{i+1:2d}/{args.runs}] ERROR    | {elapsed:.1f}s | {e}")
            results.append({"run": i + 1, "error": str(e)})

    # Summary
    valid = [r for r in results if "error" not in r and not r.get("empty")]
    print(f"\n{'='*80}")
    if valid:
        print(
            f"RESULTS: {violation_count}/{len(valid)} artifacts had F25 violations "
            f"({violation_count/len(valid)*100:.0f}%)"
        )

        pattern_counts: dict[str, int] = {}
        for r in valid:
            for v in r.get("violations", []):
                key = f"{v['symbol']}: {v['detail'][:80]}"
                pattern_counts[key] = pattern_counts.get(key, 0) + 1
        if pattern_counts:
            print(f"\nViolation patterns:")
            for desc, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
                print(f"  {count:3d}/{len(valid)} ({count/len(valid)*100:.0f}%) {desc}")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
