# R2 — Strip fences ate indentation

**Status:** resolved
**Impact:** 6/10
**Closed:** 2026-04-16

**Evidence:** Multi-method surgical artifacts output by the model mixed indent levels after the fence stripper called `.strip()` on the raw output. The first `def` ended up at column 0 while subsequent methods stayed at column 4, producing unparseable code.

**Fix:** `strategy.py:_strip_fences` no longer calls `.strip()`. It strips blank lines at the top/bottom and the fences themselves — never the leading whitespace of real code lines.
