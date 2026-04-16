# B5 — Check empty python keywords only

**Status:** resolved
**Impact:** 8/10
**Closed:** 2026-04-16

**Evidence:** After B4 skipped Python AST for TS files, `_check_empty` still rejected them because its text heuristic required `def ` or `class ` keywords. TypeScript uses `function`/`async`/`export`/`const`. Prisma uses `model`. Run_026 stuck at 50.26.

**Fix:** `validate.py:_check_empty` broadened keywords to include `function`, `async`, `export`, `const`, `let`, `var`, `model`, `interface`, `enum`, `struct`, `fn`, `pub`.

**Progress:** run_027 hit 79.50 (+7.64 from baseline 71.86).
