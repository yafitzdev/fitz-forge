# F5: Wrong Import Paths (FIXED)

## Problem
Artifacts contain import statements with wrong module paths. Example: `from fitz_sage.retrieval.rewriter.types import Query` but `Query` is actually in `fitz_sage.core`.

## Impact
- Generated code would crash with ImportError at runtime
- Sonnet scorer penalizes on codebase alignment

## Occurrence Rate
Observed in 1/3 full pipeline plans (33%). In engine.py traces, only 1/50 artifacts had codebase imports (engine.py rarely adds imports — route/API files are the main vector).

## Root Cause
The model sees class names in the structural index and decisions but doesn't always see the correct import path. It guesses the module path based on the class name and directory structure.

## Fix (IMPLEMENTED)
Deterministic post-generation import repair in `_repair_fabricated_refs()`:
1. Parse `from X import Y` statements in artifact code
2. Skip stdlib/third-party imports (typing, pydantic, etc.)
3. Look up each imported class name in the structural index
4. If the class exists but at a different module path, replace the import path

Zero LLM cost. Uses existing `StructuralIndexLookup` data.

Commit: pending

## Affected Stage
`synthesis.py` → `_repair_fabricated_refs()`, called after `_generate_single_artifact()`

## Test Data
- Engine.py traces: 1/50 had codebase imports (not enough data for F5-specific measurement)
- Full pipeline testing needed to measure route/API artifact impact

## Status: FIXED (deterministic repair)
