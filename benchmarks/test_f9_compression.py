# benchmarks/test_f9_compression.py
"""F9 harness: measure internal API fabrication rate in engine.py artifacts.

Runs the full pipeline setup once, then generates engine.py artifacts N times.
Checks each for fabricated internal calls that result from source compression
removing method bodies (model can't see how answer() chains components).

Fabrication categories:
  - Nonexistent config paths (self._config.krag.xxx)
  - Nonexistent internal methods (self._fast_analyze, self._deduplicate_addresses)
  - Nonexistent query fields (query.entity_expansion_limit, query.history)
  - Private method access (self._governor._prepare_features)
  - Wrong method signatures (self._assembler.assemble with wrong args)
  - Nonexistent factory methods (self._chat_factory.get_chat, build_messages)
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
logger = logging.getLogger("f9_test")

QUERY = (
    "Add query result streaming so answers are delivered token-by-token "
    "instead of waiting for the full response"
)

# F9-specific fabrication patterns (internal API calls the model invents
# because it can't see answer()'s 331-line body)
F9_PATTERNS = [
    # Nonexistent config paths
    (r'self\._config\.krag\.', "fabricated config: self._config.krag.*"),
    (r'self\._config\.hande\.', "fabricated config: self._config.hande.*"),
    (r'self\.config\.retrieval\.', "fabricated config: self.config.retrieval.*"),
    (r'self\.config\.expansion\.', "fabricated config: self.config.expansion.*"),
    (r'self\.config\.synthesis\.', "fabricated config: self.config.synthesis.*"),
    # Nonexistent internal methods (NOTE: _fast_analyze IS real — removed)
    (r'self\._deduplicate_addresses\(', "fabricated method: self._deduplicate_addresses()"),
    (r'self\._tag_temporal\(', "fabricated method: self._tag_temporal()"),
    (r'self\._predict_answer_mode\(', "fabricated method: self._predict_answer_mode()"),
    (r'self\._build_prompt\(', "fabricated method: self._build_prompt()"),
    # Nonexistent factory methods
    (r'self\._chat_factory\.build_messages\(', "fabricated: _chat_factory.build_messages()"),
    (r'self\._chat_factory\.get_chat\b', "fabricated: _chat_factory.get_chat()"),
    (r'self\._chat_factory\.get_chat_factory\(', "fabricated: _chat_factory.get_chat_factory()"),
    (r'self\._chat\.get_chat\(', "fabricated: _chat.get_chat()"),
    # Nonexistent query fields
    (r'query\.entity_expansion_limit', "fabricated field: query.entity_expansion_limit"),
    (r'query\.history\b', "fabricated field: query.history"),
    (r'query\.mode\b', "fabricated field: query.mode"),
    (r'query\.provider\b', "fabricated field: query.provider"),
    (r'query\.conversation_context', "fabricated field: query.conversation_context"),
    # Private method access (model guesses internal API)
    (r'\._prepare_features\(', "private access: _prepare_features()"),
    (r'\._format_block\(', "private access: _format_block()"),
    (r'\._type_group\(', "private access: _type_group()"),
    # Wrong method signatures (model guesses args)
    (r'\.assemble\(query\.text,\s*governance_reasons', "wrong sig: assemble(query.text, governance_reasons=...)"),
    (r'\.assemble\(constraint_results\)', "wrong sig: assemble(constraint_results)"),
    (r'\.assemble_conversation\(', "fabricated: assemble_conversation()"),
    (r'constraints\.create_default_constraints\(', "fabricated: create_default_constraints()"),
    # Detection orchestrator misuse
    (r'self\._detection_orchestrator\(', "fabricated: _detection_orchestrator as callable"),
    (r'self\._detection_orchestrator\.analyze\(', "fabricated: _detection_orchestrator.analyze()"),
]


def count_f9_fabrications(content: str) -> list[tuple[str, str]]:
    """Check artifact content for F9 fabrication patterns."""
    found = []
    for pattern, desc in F9_PATTERNS:
        if re.search(pattern, content):
            found.append((pattern, desc))
    return found


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()

    # Reuse the pipeline setup from test_artifact_gen
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from benchmarks.test_artifact_gen import run_pipeline_setup

    client, synth, reasoning, prior_outputs, context_merged, needed_artifacts = \
        await run_pipeline_setup()

    # Force engine.py as target
    target_file = "fitz_sage/engines/fitz_krag/engine.py"
    target_purpose = "Add streaming method to engine"

    # Get source for target
    source = synth._find_file_source(
        target_file,
        prior_outputs.get("_file_contents", {}),
        prior_outputs.get("_source_dir", ""),
    )

    # Filter decisions
    resolutions = prior_outputs.get("decision_resolution", {}).get("resolutions", [])
    relevant_decisions = synth._filter_decisions_for_file(target_file, resolutions)

    # Check what the model actually sees (compressed source size)
    from fitz_forge.planning.agent.compressor import compress_file
    compressed = compress_file(source, target_file) if len(source) > 8000 else source
    print(f"Source: {len(source)} chars -> compressed: {len(compressed)} chars "
          f"({len(compressed)/len(source)*100:.0f}%)")

    print(f"\n{'='*80}")
    print(f"F9 Fabrication Test -- {args.runs} engine.py artifacts")
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
            fabs = count_f9_fabrications(content)

            if fabs:
                fab_count += 1
                total_fabs += len(fabs)

            status = f"FAB({len(fabs):2d})" if fabs else "CLEAN   "
            print(f"[{i+1:2d}/{args.runs}] {status} | {len(content):5d} chars | "
                  f"{elapsed:.1f}s", end="")
            if fabs:
                print(f" | {fabs[0][1]}", end="")
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
    print(f"RESULTS: {fab_count}/{len(valid)} artifacts had F9 fabrications "
          f"({fab_count/len(valid)*100:.0f}%)" if valid else "No valid runs")
    print(f"Total fabrication instances: {total_fabs}")

    # Pattern frequency
    if valid:
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
