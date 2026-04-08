# V2-F6: Cross-Artifact Method Mismatch

**Occurrence:** 4/10 plans (run 83)
**Impact:** ~3 pts (consistency check failure)

## What Happens

One artifact calls a method on an object whose name matches another artifact's file, but that method isn't defined in the target artifact. Examples:

- `services/__init__.py` calls `engine.answer_stream()` but the engine.py artifact doesn't parse (so no methods are detected)
- `api/routes/query.py` calls `fitz.query_stream()` but `sdk/fitz.py` artifact doesn't define it

## Two Sub-Patterns

### A. Engine.py unparseable → "defines no methods"
When engine.py fails to parse (V2-F1), the consistency checker sees it as defining zero methods. Any artifact that calls `engine.answer_stream()` then fails the method agreement check. **This is a cascading failure from V2-F1, not an independent issue.**

### B. Genuine method name disagreement
The model uses different method names across artifacts (e.g., `query_stream` in routes but `answer_stream` in services). This is a real consistency problem.

## Relationship to Other Patterns

- Fixing V2-F1 (engine parse failure) would eliminate sub-pattern A
- The existing F3 signature injection fix (prior artifact signatures injected into subsequent artifact prompts) already addresses sub-pattern B for non-surgical artifacts. It was disabled for surgical outputs to prevent F3 leaks. Could be re-enabled with filtering.

## Potential Fixes

1. Fix V2-F1 first — eliminates cascading mismatches
2. Re-enable F3 signature injection for non-surgical artifacts
3. Post-generation rename pass: if artifact A defines `answer_stream` and artifact B calls `query_stream` on the same object, flag for reconciliation
