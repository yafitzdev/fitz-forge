# F3: Cross-Artifact Signature Mismatch (FIXED)

## Problem
Each artifact is generated independently with its own LLM call. When one artifact defines `answer_stream(query, *, progress)`, another artifact (e.g., FitzService) calls it as `engine.answer_stream(query, context, results, answer_mode, ...)` with completely different arguments. The artifacts contradict each other.

## Impact
- Generated code would crash at the integration boundary between components
- Sonnet scorer penalizes heavily on internal consistency and implementability

## Occurrence Rate
Observed in 1/3 plans (33%). Hard to measure precisely since it requires cross-artifact analysis.

## Root Cause
Per-artifact generation is intentionally isolated — each artifact gets its own generate() call with the target file's source code and relevant decisions. But this means artifact A doesn't know what artifact B defined. The model re-invents method signatures independently for each file.

## Fix (IMPLEMENTED)
**Prior artifact signature injection**: When generating artifact N, inject method signatures from artifacts 1..N-1 into the prompt as "SIGNATURES FROM OTHER ARTIFACTS (match these exactly)".

How it works:
1. After each artifact is generated, `_extract_method_signatures()` parses the code with AST
2. Public method signatures are accumulated in a list
3. The next artifact's prompt includes these signatures
4. The model sees what methods were already defined and uses matching call signatures

Example injected context:
```
## SIGNATURES FROM OTHER ARTIFACTS (match these exactly)
engine.py: async def answer_stream(self, query: Query) -> AsyncIterator[str]
engine.py: def get_status(self) -> dict
```

Zero extra LLM cost — signatures extracted via AST, no additional generation calls.

Commit: pending

## Affected Stage
`synthesis.py` → `_build_artifacts_per_file()` and `_generate_single_artifact()`

## Status: FIXED (signature injection)
