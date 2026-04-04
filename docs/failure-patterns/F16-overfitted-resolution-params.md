# F16: Overfitted Resolution Parameter List

## Problem
`planning/prompts/decision_resolution.txt` lines 25-26 hardcode exact parameter names from the fitz-sage codebase:

```
A parallel method MUST accept the SAME parameters as the original. Do not simplify
or remove parameters. If generate() takes (query, context, results, answer_mode,
gap_context, conflict_context), then generate_stream() must take those same parameters.
```

And line 42 hardcodes method names:
```
"e.g., 'generate_stream() must return Iterator[str] because chat_stream() returns Iterator[str]'"
```

## Impact
- `answer_mode`, `gap_context`, `conflict_context` are FitzKrag-specific parameter names
- `generate()`, `generate_stream()`, `chat_stream()` are FitzKrag-specific method names
- On other codebases, these examples are noise at best, misleading at worst
- The RULE itself (parallel methods must match parameters) is good — the EXAMPLE is overfitted

## Affected Stage
`decision_resolution.txt` — the resolution prompt template

## Fix
Keep the rule, replace the example with a generic placeholder. Do NOT auto-populate from codebase scan — adds complexity, reduces reproducibility.

## Test Data
- Harness: `benchmarks/test_f9_compression.py` (reused — artifact fabrication regression test)
- Baseline (with overfitted params): **0/50** (0% F9 fabrication)
- After fix (generic examples): **0/50** (0% F9 fabrication)
- No regression. The overfitted parameter list was not affecting model behavior.

## Risk
None — verified no regression.

## Status: FIXED (0/50 → 0/50, no regression)
