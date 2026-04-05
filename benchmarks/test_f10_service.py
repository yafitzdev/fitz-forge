# benchmarks/test_f10_service.py
"""F10 harness: measure FitzService API fabrication in route/SDK artifacts.

Runs the full pipeline setup once, then generates query.py artifacts N times.
Checks each for fabricated FitzService method calls.

FitzService real methods: query(), point(), list_collections(),
get_collection(), delete_collection(), validate_config(),
get_config_summary(), health_check()

Common fabrications: service.query_stream(), service.chat_stream(),
service.retrieve(), service.get_provider(), service.get_governance_decider(),
service.build_messages(), service._fast_analyze()
"""

import argparse
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
logger = logging.getLogger("f10_test")

QUERY = (
    "Add query result streaming so answers are delivered token-by-token "
    "instead of waiting for the full response"
)

# F10 fabrication patterns — methods that don't exist on FitzService
F10_PATTERNS = [
    (r'service\.query_stream\(', "service.query_stream() — doesn't exist"),
    (r'service\.chat_stream\(', "service.chat_stream() — doesn't exist"),
    (r'service\.answer_stream\(', "service.answer_stream() — doesn't exist"),
    (r'service\.generate_stream\(', "service.generate_stream() — doesn't exist"),
    (r'service\.retrieve\(', "service.retrieve() — doesn't exist"),
    (r'service\.get_provider\(', "service.get_provider() — doesn't exist"),
    (r'service\.get_governance', "service.get_governance*() — doesn't exist"),
    (r'service\.build_messages\(', "service.build_messages() — doesn't exist"),
    (r'service\._fast_analyze\(', "service._fast_analyze() — doesn't exist"),
    (r'service\.get_engine\(', "service.get_engine() — doesn't exist"),
    (r'service\.llm_provider', "service.llm_provider — doesn't exist"),
    (r'service\.stream\(', "service.stream() — doesn't exist"),
    # Also catch route-level fabrications
    (r'request\.query\b(?!_)', "request.query — ChatRequest has .message"),
    (r'TestDetection|TestConversation', "test class import in production code"),
]


def _strip_comments(content: str) -> str:
    """Remove comment lines and inline comments from Python code."""
    lines = []
    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if " #" in line:
            line = line[: line.index(" #")]
        lines.append(line)
    return "\n".join(lines)


def count_f10_fabrications(content: str) -> list[tuple[str, str]]:
    """Check artifact content for F10 fabrication patterns (code only, not comments)."""
    code_only = _strip_comments(content)
    found = []
    for pattern, desc in F10_PATTERNS:
        if re.search(pattern, code_only):
            found.append((pattern, desc))
    return found


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--target", type=str, default="query.py",
                        help="Target artifact: query.py or fitz.py")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from benchmarks.test_artifact_gen import run_pipeline_setup

    client, synth, reasoning, prior_outputs, context_merged, needed_artifacts = \
        await run_pipeline_setup()

    # Find target artifact
    target_file = None
    target_purpose = ""
    for art in needed_artifacts:
        name = str(art) if isinstance(art, str) else art.get("filename", str(art))
        if args.target in name:
            target_file = name
            if " -- " in name:
                target_file, target_purpose = name.split(" -- ", 1)
                target_file = target_file.strip()
                target_purpose = target_purpose.strip()
            break

    if not target_file:
        if "query" in args.target:
            target_file = "fitz_sage/api/routes/query.py"
            target_purpose = "Add /chat/stream and /query/stream endpoints using StreamingResponse that delegates to service"
        else:
            target_file = "fitz_sage/sdk/fitz.py"
            target_purpose = "Add query_stream() method that delegates to service for streaming"
        logger.warning(f"No artifact matching '{args.target}', forcing {target_file}")

    logger.info(f"Target: {target_file} | Purpose: {target_purpose}")

    # Get source for target
    source = synth._find_file_source(
        target_file,
        prior_outputs.get("_file_contents", {}),
        prior_outputs.get("_source_dir", ""),
    )

    # Filter decisions
    resolutions = prior_outputs.get("decision_resolution", {}).get("resolutions", [])
    relevant_decisions = synth._filter_decisions_for_file(target_file, resolutions)

    print(f"Source: {len(source)} chars")
    print(f"\n{'='*80}")
    print(f"F10 FitzService Fabrication Test -- {args.runs} {args.target} artifacts")
    print(f"{'='*80}\n")

    fab_count = 0
    total_fabs = 0
    results = []

    for i in range(args.runs):
        t0 = time.monotonic()
        try:
            artifact = await synth._generate_single_artifact(
                client, target_file, target_purpose,
                source, relevant_decisions, reasoning,
                prior_outputs=prior_outputs,
            )
            elapsed = time.monotonic() - t0
            content = artifact.get("content", "") if artifact else ""
            fabs = count_f10_fabrications(content)

            if fabs:
                fab_count += 1
                total_fabs += len(fabs)

            status = f"FAB({len(fabs):2d})" if fabs else "CLEAN   "
            print(f"[{i+1:2d}/{args.runs}] {status} | {len(content):5d} chars | "
                  f"{elapsed:.1f}s", end="")
            if fabs:
                print(f" | {fabs[0][1][:50]}", end="")
                if len(fabs) > 1:
                    print(f" +{len(fabs)-1} more", end="")
            print()

            results.append({
                "run": i + 1,
                "n_fabs": len(fabs),
                "details": [d for _, d in fabs],
                "chars": len(content),
                "elapsed": elapsed,
            })

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"[{i+1:2d}/{args.runs}] ERROR   | {elapsed:.1f}s | {e}")
            results.append({"run": i + 1, "error": str(e)})

    # Summary
    valid = [r for r in results if "error" not in r]
    print(f"\n{'='*80}")
    if valid:
        print(f"RESULTS: {fab_count}/{len(valid)} artifacts had F10 fabrications "
              f"({fab_count/len(valid)*100:.0f}%)")
        print(f"Total fabrication instances: {total_fabs}")

        pattern_counts: dict[str, int] = {}
        for r in valid:
            for d in r.get("details", []):
                pattern_counts[d] = pattern_counts.get(d, 0) + 1
        if pattern_counts:
            print(f"\nTop fabrication patterns:")
            for desc, count in sorted(pattern_counts.items(), key=lambda x: -x[1])[:10]:
                print(f"  {count:3d}/{len(valid)} ({count/len(valid)*100:.0f}%) {desc}")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
