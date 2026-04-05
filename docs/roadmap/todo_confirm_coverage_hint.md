# TODO: Confirm coverage hint fires for call chain gaps

## Context

We fixed the import graph to resolve relative imports (`from .foo import X`) and increased
the BFS node cap from 80 to 200 so intermediate files like `fitz_service.py` appear in the
call graph as interior nodes (depth > 0).

The existing `_build_coverage_hint` mechanism in `DecisionDecompositionStage` is supposed to
detect when the decomposition skips intermediate call chain layers and retry with a hint.
But we haven't verified it actually fires.

## What to verify

1. **Call graph has FitzService as interior node**: With the relative import fix,
   `fitz_sage/services/__init__.py` -> `fitz_sage/services/fitz_service.py` should appear
   at depth 1 in the call graph (reachable via `query.py -> dependencies.py -> services/__init__.py`).

2. **Coverage hint fires when FitzService is missing**: Generate a decomposition that skips
   `fitz_service.py` in `relevant_files`. Confirm `_build_coverage_hint` returns a non-empty
   string listing it as uncovered.

3. **Coverage retry improves the decomposition**: Confirm the retry produces decisions that
   include `fitz_service.py` or `services/__init__.py` in `relevant_files`.

## How to verify

Option A: Unit test — mock a call graph with interior FitzService node, mock decisions that
skip it, assert `_build_coverage_hint` returns a hint.

Option B: Run 10 plans, grep logs for "coverage gap detected, retrying". If it never fires,
either the decomposition already covers the chain (good) or the hint check isn't detecting
gaps (bad — need to investigate threshold at line 110: `len(uncovered) * 2 < len(interior)`).

## Files

- `fitz_forge/planning/pipeline/stages/decision_decomposition.py` — `_build_coverage_hint` (line 76)
- `fitz_forge/planning/agent/indexer.py` — `_extract_full_imports` (relative import fix)
- `fitz_forge/planning/pipeline/call_graph.py` — `extract_call_graph` (max_nodes=200)
