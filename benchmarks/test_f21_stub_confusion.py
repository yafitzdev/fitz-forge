# benchmarks/test_f21_stub_confusion.py
"""F21 harness: measure stub confusion in engine.py artifacts.

Runs the full pipeline setup once (freezes decisions), then generates
engine.py artifacts N times. Checks each for F21 indicators:
1. Calls generate() (blocking) instead of generate_stream() — treats it as a stub
2. Calls chat_stream() directly bypassing synthesizer — treats synthesis as a stub
3. Fabricates replacement methods (_build_messages_for_*, assemble_messages, etc.)
4. Contains NotImplementedError — gave up because it thinks methods are stubs

The fix: change `...  # N lines` in compressor to a comment format that
can't be confused with Python's Ellipsis stub convention.
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
logger = logging.getLogger("f21_test")

QUERY = (
    "Add query result streaming so answers are delivered token-by-token "
    "instead of waiting for the full response"
)

# F21 indicators — signs the model thinks methods are stubs
F21_INDICATORS = [
    # Calls blocking generate() when it should create generate_stream()
    (r"self\._synthesizer\.generate\(", "calls blocking generate() — treats it as the only option"),
    # Bypasses synthesizer entirely, calls chat_stream directly on provider
    (r"self\._chat\.chat_stream\(", "calls chat_stream() directly — bypasses synthesizer"),
    (r"chat_provider\.chat_stream\(", "calls chat_stream() on provider — bypasses synthesizer"),
    # Fabricates replacement methods (doesn't trust existing ones)
    (r"_build_messages_for", "fabricates _build_messages_for* — doesn't trust existing methods"),
    (r"assemble_messages\(", "fabricates assemble_messages() — doesn't trust existing methods"),
    # Gives up entirely
    (r"NotImplementedError", "raises NotImplementedError — gave up"),
    # Explicitly says method is a stub
    (r"stub|unimplemented|not implemented|no body|no implementation",
     "explicitly claims method is a stub"),
]


def check_f21_indicators(content: str) -> list[tuple[str, str]]:
    """Check artifact content for F21 stub confusion indicators."""
    found = []
    # Strip comments to avoid false positives on our own docstrings
    code_lines = []
    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code_only = "\n".join(code_lines)

    for pattern, desc in F21_INDICATORS:
        if re.search(pattern, code_only, re.IGNORECASE):
            found.append((pattern, desc))
    return found


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from benchmarks.test_artifact_gen import run_pipeline_setup

    client, synth, reasoning, prior_outputs, context_merged, needed_artifacts = \
        await run_pipeline_setup()

    # Target engine.py (where F21 is most impactful)
    target_file = "fitz_sage/engines/fitz_krag/engine.py"
    target_purpose = (
        "Add answer_stream() method that yields token deltas "
        "for streaming (parallel to existing answer())"
    )

    # Check if we can find a better purpose from extracted artifacts
    for art in needed_artifacts:
        name = str(art) if isinstance(art, str) else art.get("filename", str(art))
        if "engine.py" in name:
            if " -- " in name:
                _, target_purpose = name.split(" -- ", 1)
                target_purpose = target_purpose.strip()
            break

    logger.info(f"Target: {target_file} | Purpose: {target_purpose}")

    source = synth._find_file_source(
        target_file,
        prior_outputs.get("_file_contents", {}),
        prior_outputs.get("_source_dir", ""),
    )
    resolutions = prior_outputs.get("decision_resolution", {}).get("resolutions", [])
    relevant_decisions = synth._filter_decisions_for_file(target_file, resolutions)

    # Check what the compressed source looks like
    if len(source) > 8000:
        from fitz_forge.planning.agent.compressor import compress_file
        compressed = compress_file(source, target_file)
        ellipsis_count = compressed.count("...  #")
        logger.info(
            f"Source: {len(source)} chars -> {len(compressed)} compressed, "
            f"{ellipsis_count} ellipsis markers"
        )
    else:
        ellipsis_count = 0

    print(f"Source: {len(source)} chars, {ellipsis_count} ellipsis markers")
    print(f"\n{'='*80}")
    print(f"F21 Stub Confusion Test — {args.runs} engine.py artifacts")
    print(f"{'='*80}\n")

    f21_count = 0
    total_indicators = 0
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
            indicators = check_f21_indicators(content)

            if indicators:
                f21_count += 1
                total_indicators += len(indicators)

            status = f"F21({len(indicators):2d})" if indicators else "CLEAN    "
            print(
                f"[{i+1:2d}/{args.runs}] {status} | {len(content):5d} chars | "
                f"{elapsed:.1f}s",
                end="",
            )
            if indicators:
                print(f" | {indicators[0][1][:50]}", end="")
                if len(indicators) > 1:
                    print(f" +{len(indicators)-1} more", end="")
            print()

            results.append({
                "run": i + 1,
                "n_indicators": len(indicators),
                "details": [d for _, d in indicators],
                "chars": len(content),
                "elapsed": elapsed,
            })

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"[{i+1:2d}/{args.runs}] ERROR    | {elapsed:.1f}s | {e}")
            results.append({"run": i + 1, "error": str(e)})

    # Summary
    valid = [r for r in results if "error" not in r]
    print(f"\n{'='*80}")
    if valid:
        print(
            f"RESULTS: {f21_count}/{len(valid)} artifacts had F21 indicators "
            f"({f21_count/len(valid)*100:.0f}%)"
        )
        print(f"Total indicator instances: {total_indicators}")

        pattern_counts: dict[str, int] = {}
        for r in valid:
            for d in r.get("details", []):
                pattern_counts[d] = pattern_counts.get(d, 0) + 1
        if pattern_counts:
            print(f"\nTop indicators:")
            for desc, count in sorted(pattern_counts.items(), key=lambda x: -x[1])[:10]:
                print(f"  {count:3d}/{len(valid)} ({count/len(valid)*100:.0f}%) {desc}")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
