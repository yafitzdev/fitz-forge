# F17: Overfitted Synthesis Citation Examples

## Problem
`planning/prompts/synthesis.txt` lines 22-23 hardcode codebase-specific examples:

```
- Reference each decision by its ID when you use it (e.g., "Per [d3], the streaming
  interface returns AsyncIterator[str]").
- Always name specific classes, methods, and files (e.g., "`FitzKragEngine.answer()`
  in `engine.py`" not "the engine's answer method").
```

## Impact
- `FitzKragEngine`, `answer()`, `engine.py`, `AsyncIterator[str]` are all from one codebase
- The citation RULE is excellent — the EXAMPLES are overfitted
- On other codebases, these examples could prime the model to use similar naming patterns

## Affected Stage
`synthesis.txt` — the synthesis reasoning prompt template

## Fix
Replace with generic placeholder examples. Do NOT auto-populate from codebase scan — adds complexity, reduces reproducibility.

## Test Data
- Harness: `benchmarks/test_f9_compression.py` (reused — tested jointly with F16)
- Baseline (with overfitted examples): **0/50** (0% F9 fabrication)
- After fix (generic examples): **0/50** (0% F9 fabrication)
- No regression.

## Risk
None — verified no regression.

## Status: FIXED (0/50 → 0/50, no regression)
