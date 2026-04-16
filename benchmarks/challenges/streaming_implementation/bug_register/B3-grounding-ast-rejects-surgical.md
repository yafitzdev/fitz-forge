# B3 — Grounding ast rejects surgical

**Status:** resolved
**Impact:** 4/10
**Closed:** 2026-04-16

**Evidence:** Grounding used raw `ast.parse` while closure had a class-wrap recovery in `try_parse`. Surgical artifacts (indented method bodies) failed grounding's parser silently and were skipped for validation.

**Fix:** `grounding/check.py` — both `check_artifact` and `_check_parallel_signatures` now call `try_parse` from `grounding/inference.py` so class-wrapped surgical output parses consistently with closure.
