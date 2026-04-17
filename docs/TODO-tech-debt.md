# Tech-debt todo list

Tracking known issues. The tree-sitter migration scaffolding has been
fully demolished — the codebase runs only on tree-sitter, with no
engine flag and no ast-based fallback. Items 2 and 3 below are quirks
the port deliberately preserved for byte-parity; fixing them is a
post-migration correctness improvement.

## Tree-sitter migration follow-ups

### 1. Delete the Python-ast path in `grounding/` — **DONE (2026-04-17)**

All ast-based code in the grounding / agent / artifact pipelines has
been removed. `grounding/inference.py` is now tree-sitter only;
`grounding/parser.py` exposes `parse_python` (the former
`_ts_parser`). The engine flag (`set_engine`, `get_engine`, `_ENGINE`)
is gone. `try_parse` is replaced by `parse_python`. Parity test files
have been deleted.

### 2. Fix: nested-function skip guard was ineffective in the ast port

**What:** Several functions in the original ast-based inference module
contained a pattern like:

```python
for child in ast.walk(node):
    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
        continue
```

The intent was to skip nested function bodies. `ast.walk` had already
queued the nested function's descendants before `continue` fired, so
nested `return`/`yield` leaked into the outer function's inference.
The tree-sitter port in `inference.py` preserved this behaviour via
`_iter_body_skipping_nested`, which *does* walk into nested functions
to match the original semantics.

**Where:** `inference._infer_return_from_body`,
`inference._infer_return_from_yields`,
`inference.extract_init_self_attrs`.

**Impact:** A function containing a nested `return Foo()` will
confuse `infer_return_type` on the outer function. Low frequency in
real code (nested factory functions are rare), but a latent
correctness hole.

**Fix:** Change `_iter_body_skipping_nested` to genuinely skip nested
`function_definition` subtrees. No callers currently depend on the
buggy behaviour, so this can be a straight fix.

### 3. Fix: index pass1 misses top-level `async def`

**What:** `inference.iter_top_level_functions` intentionally skips
``async def`` at module top level to match the ast pass1 quirk
(`isinstance(node, ast.FunctionDef)` excluded `AsyncFunctionDef`).
Top-level `async def` functions (e.g. `async def _get_store()` in
`cli.py`) never enter the index.

**Impact:** Callers that look up an async function by name get no
result. Downstream closure checks can flag correct references as
missing.

**Fix:** Remove the `_function_is_async` filter in
`iter_top_level_functions` so async top-level functions are indexed.

### 5. Port ``planning/pipeline/stages/synthesis.py`` — **DONE (prior session)**

`synthesis.py` now uses the same tree-sitter helpers as the rest of
the pipeline. See commits `94b4175`, `ab08000`, `c188005`.

## Compatibility / legacy shims

### 4. Remove compatibility shims in the codebase

**Where to look:**
- Any `# noqa: legacy` or `# compat` comments.
- Re-exports in `__init__.py` files flagged as "keep for backward
  compat" — especially `fitz_forge/planning/validation/grounding/__init__.py`.
- Conditional imports like `try: import X; except ImportError:` used
  to support old versions of an internal package.
- Renamed types re-exported under an old name.

**Action:** Grep for the markers above, delete with the calling code
where possible, update call sites.

**Why not now:** Deletions want a single atomic commit per shim so
blame history stays honest. Doing them during the tree-sitter
migration would conflate two unrelated cleanups.
