# benchmarks/test_f6_empty.py
"""F6 harness: run N field group extractions, measure empty-result rate.

Generates ONE synthesis reasoning, then runs extraction N times from it.
Checks all critical field groups: phases, approaches, components.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.stderr.write("")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("f6_empty")

QUERY = (
    "Add query result streaming so answers are delivered token-by-token "
    "instead of waiting for the full response"
)

# Critical field groups to test
CRITICAL_GROUPS = [
    {
        "label": "phases",
        "fields": ["phases"],
        "schema": json.dumps({
            "phases": [
                {
                    "number": 1,
                    "name": "Phase Name",
                    "objective": "What this phase achieves",
                    "deliverables": ["specific deliverable"],
                    "dependencies": [],
                    "estimated_complexity": "low|medium|high",
                    "key_risks": ["risk"],
                    "verification_command": "pytest tests/test_something.py -v",
                    "estimated_effort": "~2 hours",
                }
            ],
        }, indent=2),
        "critical_field": "phases",
    },
    {
        "label": "approaches",
        "fields": ["approaches", "scope_statement", "technical_constraints"],
        "schema": json.dumps({
            "approaches": [
                {
                    "name": "Approach Name",
                    "description": "How it works",
                    "pros": ["advantage"],
                    "cons": ["disadvantage"],
                    "recommended": True,
                }
            ],
            "scope_statement": "What is in and out of scope",
            "technical_constraints": ["constraint"],
        }, indent=2),
        "critical_field": "approaches",
    },
    {
        "label": "components",
        "fields": ["components", "data_model"],
        "schema": json.dumps({
            "components": [
                {
                    "name": "ComponentName",
                    "purpose": "What it does",
                    "responsibilities": ["responsibility"],
                    "interfaces": ["methodName(param: Type) -> ReturnType"],
                    "dependencies": ["OtherComponent"],
                }
            ],
            "data_model": {"EntityName": ["field: type"]},
        }, indent=2),
        "critical_field": "components",
    },
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()

    from fitz_forge.config import load_config
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.planning.agent import AgentContextGatherer
    from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage

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

    # Generate ONE synthesis reasoning
    stage = SynthesisStage()
    krag_context = stage._get_gathered_context(prior_outputs)

    # Build a minimal reasoning prompt (we need reasoning text to extract from)
    print("Generating synthesis reasoning (one-time)...")
    reasoning_messages = stage.build_prompt(QUERY, prior_outputs)
    t0 = time.monotonic()
    reasoning = await client.generate(
        messages=reasoning_messages, temperature=0, max_tokens=16384,
    )
    print(f"Reasoning generated in {time.monotonic() - t0:.1f}s ({len(reasoning)} chars)\n")

    # Now test each critical group N times
    for group in CRITICAL_GROUPS:
        label = group["label"]
        critical_field = group["critical_field"]

        print(f"{'='*80}")
        print(f"Testing '{label}' extraction — {args.runs} runs")
        print(f"{'='*80}")

        empty_count = 0
        error_count = 0

        for i in range(args.runs):
            t0 = time.monotonic()
            try:
                partial = await stage._extract_field_group(
                    client, reasoning, group["fields"],
                    group["schema"], label,
                    extra_context=krag_context if label != "phases" else "",
                )
                elapsed = time.monotonic() - t0
                field_val = partial.get(critical_field, [])
                is_empty = not field_val or (isinstance(field_val, list) and len(field_val) == 0)

                if is_empty:
                    empty_count += 1
                    print(f"  [{i+1:2d}/{args.runs}] EMPTY  | {elapsed:.1f}s")
                else:
                    count = len(field_val) if isinstance(field_val, list) else "n/a"
                    print(f"  [{i+1:2d}/{args.runs}] ok({count:>3}) | {elapsed:.1f}s")

            except Exception as e:
                elapsed = time.monotonic() - t0
                error_count += 1
                print(f"  [{i+1:2d}/{args.runs}] ERROR  | {elapsed:.1f}s | {e}")

        valid = args.runs - error_count
        print(f"  >> {label}: {empty_count}/{valid} empty ({empty_count/valid*100:.0f}%)" if valid else "  >> no valid runs")
        print()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
