# B4 — Python ast rejects ts

**Status:** resolved
**Impact:** 10/10
**Closed:** 2026-04-16

**Evidence:** After B2 made the model output real multi-line TypeScript, `_check_parseable` rejected every TS file (Python AST can't parse TypeScript decorators, arrow functions, type annotations). Run_025 regressed to 44.71.

**Generalization:** the artifact pipeline was gated on Python AST succeeding — which rejected every non-Python file outright.

**Fix:** `validate.py:_is_python_file`. Python AST-based checks skip for `.ts`/`.js`/`.go`/`.rs`/`.java`/`.prisma` files. Structural validation remains Python-only — full multi-language validation via tree-sitter is on the roadmap.
