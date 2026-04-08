# F18: Overfitted Artifact Generation Rules

## Problem
`synthesis.py` artifact prompt rules contain a streaming-specific example:

```python
"- When adding a parallel method (e.g. generate_stream), "
"match the original method's parameters\n"
```

`generate_stream` is a method name from the fitz-sage codebase.

## Impact
- Primes the model to use `_stream` suffix convention
- On codebases with different naming conventions (e.g. `_async`, `_batch`, `_v2`), this could mislead
- The RULE (match original parameters) is good — the EXAMPLE is overfitted

## Affected Stage
`synthesis.py` → `_generate_single_artifact()` rules section

## Fix
Remove the codebase-specific example:
```python
"- When adding a parallel method, match the original method's parameters\n"
```

## Test Data
- Harness: `benchmarks/test_f9_compression.py` (reused — artifact generation test)
- Baseline: **0/50** (0% fabrication with overfitted example)
- After fix: **0/50** (0% fabrication without overfitted example)
- No regression. The example was pure noise.

## Risk
None — verified no regression.

## Status: FIXED (0/50 → 0/50, no regression)
