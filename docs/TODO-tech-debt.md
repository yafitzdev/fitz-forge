# Tech-debt todo list

Tracking known issues to clean up once the tree-sitter migration has
soaked. All items here are deliberate compromises the migration made
for strict byte-parity with the existing Python-ast backend — fixing
them is a separate, post-migration concern.

## Tree-sitter migration follow-ups

### 1. Delete the Python-ast path in `grounding/`

**What:** `fitz_forge/planning/validation/grounding/index.py` and
`inference.py` still contain the full ast-backed implementation. They
only run when `set_engine("ast")` is called — the default is now
`tree_sitter`. Once tree-sitter has soaked in production without
drift, delete:

- `StructuralIndexLookup.augment_from_source_dir` (ast branch), plus
  `_absorb_file_pass1`, `_absorb_file_pass2`, `_absorb_class`.
- `inference.try_parse` (replaced by `_ts_parser.parse_python`).
- `inference.extract_type_name`, `unparse_annotation`,
  `class_name_of_expr`, `infer_return_type`, `_infer_return_from_body`,
  `_infer_return_from_yields`, `_infer_return_from_docstring`,
  `extract_class_fields`, `extract_init_self_attrs`.
- `import ast` from `index.py` and `inference.py`.
- `set_engine` / `get_engine` / `_ENGINE` on `index.py` — no more
  routing needed.
- Tests that exercise the ast-only path explicitly.

**Why not now:** Keeps a clear rollback lever
(`set_engine("ast")`) during the soak period.

### 2. Fix: `ast.walk` skip-nested-function guard is ineffective

**What:** Several functions in `inference.py` (and their tree-sitter
ports) contain a pattern like:

```python
for child in ast.walk(node):
    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
        continue
```

The intent is to skip nested function bodies. In practice
`ast.walk` has already queued the nested function's descendants before
we can `continue`, so nested `return`/`yield` leaks into the outer
function's inference.

**Where:** `inference._infer_return_from_body`, `inference._infer_return_from_yields`,
`inference.extract_init_self_attrs` walk loop.

**Impact:** A function containing a nested `return Foo()` will confuse
`infer_return_type` on the outer function. Low frequency in real code
(nested factory functions are rare), but it's a latent correctness
hole.

**Fix:** Switch to an iterative walk that doesn't descend into nested
`FunctionDef`/`AsyncFunctionDef`. The tree-sitter port already has
the scaffolding (`_iter_body_skipping_nested`) — currently tuned to
match the buggy behaviour.

### 3. Fix: ast pass1 misses `AsyncFunctionDef` at top level

**What:** `StructuralIndexLookup._absorb_file_pass1` only checks
`isinstance(node, ast.FunctionDef)` when indexing module-level
functions. Top-level `async def` functions (e.g.
`async def _get_store()` in `cli.py`) never enter the index.

**Impact:** Callers that look up an async function by name get no
result. Downstream closure checks can flag correct references as
missing.

**Fix:** Add `ast.AsyncFunctionDef` to the isinstance check; the
tree-sitter port already needs to un-skip `async`-flagged functions
once this is addressed.

### 5. Port ``planning/pipeline/stages/synthesis.py`` (optional)

**Status:** intentionally NOT ported to tree-sitter.

**Why it's skipped:**
- synthesis.py generates *Python* artifacts; it never processes TS/Go/etc.
  source, so the tree-sitter value proposition (cross-language parsing)
  doesn't apply.
- 35 ast sites spread across 15 functions, each with dense pattern
  matching (``ImportFrom`` + ``AnnAssign`` + ``FunctionDef`` mixed),
  roughly 6-8 hours of careful translation with no runtime parity
  benefit.
- Existing tests exercise these functions indirectly via integration
  tests that require real LLM calls, so a cheap fixture-level parity
  gate isn't available. Porting without that gate would ship subtle
  regressions.

**When to port:** if synthesis.py ever needs to process non-Python
artifacts, port it then. Otherwise leave the ``ast`` usage in place —
it's the correct tool for Python-only source analysis. The engine
flag in ``grounding.index`` does not affect this file.

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
