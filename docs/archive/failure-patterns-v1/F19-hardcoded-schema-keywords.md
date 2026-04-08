# F19: Hardcoded Schema Class Keywords

## Problem
`synthesis.py` `_resolve_imported_type_apis()` around line 1995 hardcodes domain-specific keywords for schema class lookup:

```python
candidates.update(
    name
    for name in lookup.classes
    if any(kw in name.lower() for kw in ("request", "response", "query", "chat", "answer"))
)
```

## Impact
- On fitz-sage: finds `ChatRequest`, `QueryResponse`, `Answer` etc. — works perfectly
- On a game engine codebase: misses `GameState`, `PlayerAction`, `RenderFrame`
- On a financial codebase: misses `Transaction`, `OrderBook`, `Portfolio`
- Completely biased toward chat/LLM domain

## Affected Stage
`synthesis.py` → `_resolve_imported_type_apis()` class candidate selection

## Fix
Instead of keyword matching, use a structural approach:
- Include ALL classes that are used as type annotations in the target file's function signatures
- Or include all classes from imports that appear in the file

## Risk
Medium — removing the keyword filter might include too many irrelevant classes, bloating the prompt. Need to test.

## Test Data
- Harness: `benchmarks/test_f10_service.py` (reused — route artifact fabrication)
- Baseline (with keyword filter): **0/50** (0% F10 fabrication)
- After removing keyword filter: **72% fabrication** — massive regression
- The keyword filter provides `ChatRequest fields: message, ...` which prevents `request.query` fabrication
- Removing it causes the model to lose schema grounding for request/response types
- **REVERTED** — keyword filter is load-bearing

## Better Fix (TODO)
Instead of hardcoded domain keywords, include all classes from the structural index that appear as type annotations in the target file's function signatures or imports. This is precise and codebase-agnostic but more complex to implement.

## Status: DEFERRED (removal regressed 0%→72%, needs different approach)
