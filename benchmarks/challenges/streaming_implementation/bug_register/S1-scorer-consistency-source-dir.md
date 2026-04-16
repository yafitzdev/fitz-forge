# S1 — Scorer consistency source dir

**Status:** resolved
**Impact:** 5/10
**Closed:** 2026-04-16

**Evidence:** The deterministic scorer's `check_cross_artifact_consistency` check only used the `structural_index` parameter (often truncated retrieval subset) and didn't augment from disk. Surgical artifacts calling real codebase methods were flagged as cross-artifact inconsistencies.

**Fix:** `benchmarks/eval_v2_deterministic.py:check_cross_artifact_consistency` + `run_deterministic_checks`. `source_dir` threaded through so the codebase-method skip list uses full-disk scan. Fresh 5-plan run 20 re-score: 93.10 → 97.78 after this fix.
