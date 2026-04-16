# B2 — One line signatures vs implementations

**Status:** resolved
**Impact:** 7/10
**Closed:** 2026-04-16

**Evidence:** Baseline plan_05 — 6 artifacts, avg 1 line each. `createCollectionShortcode(collectionId: string): Promise<E.Either<...>>` instead of full method bodies. The model interpreted "write ONLY the new or modified code" as "write the method signature."

**Generalization:** applies to any language where signatures and implementations are syntactically distinct.

**Fix:** `strategy.py:_RAW_CODE_INSTRUCTION` no longer says "Python code" — now says "code (full implementation, not just signatures or stubs)". NewCodeStrategy rule explicitly requests "FULL method/function body with implementation logic."

**Caveat:** this fix initially caused a regression (run_025: 44.71) because real multi-line TS output started failing Python AST. Recovered by language-aware validation dispatch (B4).
