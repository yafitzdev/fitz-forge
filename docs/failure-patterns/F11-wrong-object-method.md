# F11: Wrong Object for Correct Method

## Problem
The model calls a real method but on the wrong object:
- `self._build_provenance(results)` — real method but on `self._synthesizer`, not `self`
- `self._needs_rewriting(query)` — real method but on `self._query_rewriter`, not `self`
- `self._build_disputed_instruction(context)` — on `self._synthesizer`, not `self`

## Impact
- AttributeError at runtime despite the method existing in the codebase
- Scorer flags as codebase misalignment
- Less severe than F10 (method exists, just wrong receiver)

## Occurrence Rate
2/10 plans in run 63 (plans 5, 6). Minor contributor.

## Root Cause
The F9 reference body shows `answer()` calling `self._synthesizer._build_provenance(...)`. When the model creates `answer_stream()`, it sometimes promotes these to `self._build_provenance()` — dropping the intermediate object.

The interface injection shows `self._synthesizer -> CodeSynthesizer: generate(...)` but doesn't show all the PRIVATE methods the model sees in the reference body.

## Fix Options
1. **Already partially solved**: The F9 reference body shows the correct `self._synthesizer._build_provenance()` call. Most plans get this right (8/10). Low priority.
2. **Post-generation repair**: Check for method calls on `self` that exist on a different object, rewrite to correct receiver.

## Status: NOT FIXED (low priority — 80% of plans get it right)
