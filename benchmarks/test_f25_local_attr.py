# benchmarks/test_f25_local_attr.py
"""F25 harness: measure unvalidated local variable attribute access in artifacts.

Runs the full pipeline setup once, then generates query.py artifacts N times.
Checks each for wrong attribute access on typed local variables by:
1. AST-parsing the generated code
2. Resolving parameter type annotations (e.g., request: ChatRequest)
3. Looking up the type's fields in the structural index
4. Flagging any attribute access that doesn't match a known field/method

This catches: request.question on ChatRequest (should be request.message),
request.conversation_history (should be request.history), etc.
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
logger = logging.getLogger("f25_test")

QUERY = (
    "Add query result streaming so answers are delivered token-by-token "
    "instead of waiting for the full response"
)


def _resolve_param_types(tree: ast.AST) -> dict[str, str]:
    """Extract parameter name -> type annotation from function defs.

    Returns e.g. {"request": "ChatRequest", "service": "FitzService"}
    """
    type_map: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in node.args.args:
            if arg.annotation:
                if isinstance(arg.annotation, ast.Name):
                    type_map[arg.arg] = arg.annotation.id
                elif isinstance(arg.annotation, ast.Attribute):
                    # e.g., schemas.ChatRequest
                    type_map[arg.arg] = arg.annotation.attr
    return type_map


def _resolve_assigned_types(tree: ast.AST) -> dict[str, str]:
    """Extract variable name -> type from assignments like `service = get_service()`.

    Heuristic: if RHS is a call to a function containing a type name,
    infer the type. E.g., get_service() -> FitzService (via naming convention).
    """
    type_map: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        # Check for annotated assignment type hint
        # (handled separately via ast.AnnAssign, but let's check calls)
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            func_name = node.value.func.id
            # get_service() -> FitzService convention
            if "service" in func_name.lower():
                type_map[target.id] = "FitzService"
    return type_map


def _extract_class_fields_from_index(
    class_name: str,
    structural_index: str,
) -> set[str]:
    """Look up a class's fields/methods from the structural index.

    The structural index has lines like:
    classes: ChatRequest [message, history, collection, top_k, ...]
    """
    fields: set[str] = set()

    # Pattern: class_name appears in a classes: line with [...] listing methods/fields
    # Format: ClassName [method1, method2, ...]
    # or: ClassName(Base) [method1 -> RetType, method2, ...]
    for line in structural_index.split("\n"):
        if class_name not in line:
            continue
        # Match the [contents] block after the class name
        pattern = rf'\b{re.escape(class_name)}(?:\([^)]*\))?\s*\[([^\]]+)\]'
        m = re.search(pattern, line)
        if m:
            items = m.group(1)
            for item in items.split(","):
                item = item.strip()
                # Strip return type annotations like "method -> Type"
                if " -> " in item:
                    item = item.split(" -> ")[0].strip()
                # Strip argument lists like "method(args)"
                if "(" in item:
                    item = item.split("(")[0].strip()
                if item and not item.startswith("_"):
                    fields.add(item)
                elif item:
                    fields.add(item)

    return fields


def check_local_attr_violations(
    content: str,
    structural_index: str,
) -> list[dict]:
    """Check artifact code for wrong attribute access on typed local variables.

    Returns list of violations: {var, attr, type, known_fields, line}
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [{"error": "SyntaxError", "line": 0}]

    # Build type map from params + assignments
    type_map = _resolve_param_types(tree)
    type_map.update(_resolve_assigned_types(tree))

    if not type_map:
        return []

    # Look up fields for each known type
    type_fields: dict[str, set[str]] = {}
    for var_name, type_name in type_map.items():
        fields = _extract_class_fields_from_index(type_name, structural_index)
        if fields:
            type_fields[var_name] = fields

    if not type_fields:
        return []

    # Walk AST and check attribute access on typed variables
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue

        var_name = node.value.id
        attr_name = node.attr

        if var_name not in type_fields:
            continue

        known = type_fields[var_name]
        # Skip dunder and private attrs (we don't index those reliably)
        if attr_name.startswith("__"):
            continue

        if attr_name not in known:
            violations.append({
                "var": var_name,
                "attr": attr_name,
                "type": type_map[var_name],
                "known_fields": sorted(known),
                "line": getattr(node, "lineno", 0),
            })

    return violations


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--target", type=str, default="query.py",
        help="Target artifact: query.py, fitz.py, engine.py",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from benchmarks.test_artifact_gen import run_pipeline_setup
    from fitz_forge.planning.pipeline.stages.synthesis import (
        _CONTEXT_FIELD_GROUPS,
    )

    client, synth, reasoning, prior_outputs, context_merged, needed_artifacts = \
        await run_pipeline_setup()

    # Get structural index for field lookup
    agent_ctx = prior_outputs.get("_agent_context", {})
    structural_index = agent_ctx.get("full_structural_index", "")
    if not structural_index:
        structural_index = prior_outputs.get("_gathered_context", "")
        if not structural_index:
            structural_index = prior_outputs.get("_full_structural_index", "")

    if not structural_index:
        print("ERROR: no structural index available")
        return

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
            target_purpose = (
                "Add /chat/stream endpoint using StreamingResponse "
                "that delegates to service for streaming"
            )
        else:
            target_file = args.target
            target_purpose = "Add streaming variant"
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
    print(f"Structural index: {len(structural_index)} chars")
    print(f"\n{'='*80}")
    print(f"F25 Local Attr Validation Test -- {args.runs} {args.target} artifacts")
    print(f"Each run regenerates reasoning (not frozen-state)")
    print(f"{'='*80}\n")

    violation_count = 0
    total_violations = 0
    results = []

    for i in range(args.runs):
        t0 = time.monotonic()
        try:
            # Regenerate reasoning each run to vary upstream state
            messages = synth.build_prompt(QUERY, prior_outputs)
            run_reasoning = await client.generate(
                messages=messages, temperature=0.7,
            )

            # Re-extract needed_artifacts from this reasoning
            krag_context = synth._get_gathered_context(prior_outputs)
            run_context = {}
            for group in _CONTEXT_FIELD_GROUPS:
                extra = krag_context if group["label"] in {"files", "description"} else ""
                partial = await synth._extract_field_group(
                    client, run_reasoning, group["fields"],
                    group["schema"], group["label"],
                    extra_context=extra,
                )
                run_context.update(partial)

            # Find purpose for this run's artifacts
            run_needed = run_context.get("needed_artifacts", [])
            run_purpose = target_purpose
            for art in run_needed:
                name = str(art) if isinstance(art, str) else art.get("filename", str(art))
                if args.target in name:
                    if " -- " in name:
                        _, run_purpose = name.split(" -- ", 1)
                        run_purpose = run_purpose.strip()
                    break

            artifact = await synth._generate_single_artifact(
                client, target_file, run_purpose,
                source, relevant_decisions, run_reasoning,
                prior_outputs=prior_outputs,
            )
            elapsed = time.monotonic() - t0
            content = artifact.get("content", "") if artifact else ""
            violations = check_local_attr_violations(content, structural_index)

            # Filter out error-type violations (SyntaxError etc)
            real_violations = [v for v in violations if "var" in v]
            syntax_errors = [v for v in violations if "error" in v]

            if real_violations:
                violation_count += 1
                total_violations += len(real_violations)

            status = f"VIOL({len(real_violations):2d})" if real_violations else "CLEAN    "
            if syntax_errors:
                status = "SYNTAX   "
            print(
                f"[{i+1:2d}/{args.runs}] {status} | {len(content):5d} chars | "
                f"{elapsed:.1f}s",
                end="",
            )
            if real_violations:
                v = real_violations[0]
                print(
                    f" | {v['var']}.{v['attr']} (type={v['type']}, "
                    f"known={v['known_fields'][:5]})",
                    end="",
                )
                if len(real_violations) > 1:
                    print(f" +{len(real_violations)-1} more", end="")
            print()

            violations = real_violations

            results.append({
                "run": i + 1,
                "n_violations": len(violations),
                "violations": violations,
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
            f"RESULTS: {violation_count}/{len(valid)} artifacts had F25 violations "
            f"({violation_count/len(valid)*100:.0f}%)"
        )
        print(f"Total violation instances: {total_violations}")

        # Group by var.attr pattern
        pattern_counts: dict[str, int] = {}
        for r in valid:
            for v in r.get("violations", []):
                key = f"{v['var']}.{v['attr']} (type={v['type']})"
                pattern_counts[key] = pattern_counts.get(key, 0) + 1
        if pattern_counts:
            print(f"\nTop violation patterns:")
            for desc, count in sorted(
                pattern_counts.items(), key=lambda x: -x[1]
            )[:10]:
                print(f"  {count:3d}/{len(valid)} ({count/len(valid)*100:.0f}%) {desc}")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
