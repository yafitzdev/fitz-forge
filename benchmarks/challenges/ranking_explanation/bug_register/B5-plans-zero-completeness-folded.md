# B5 — Plans zero completeness folded

**Status:** resolved
**Impact:** 3/10
**Closed:** 2026-04-16

**Evidence:** Baseline plans 03, 07 had 0-15 completeness despite having resolved decisions. Missing schemas.py or query.py. Synthesis reasoning decided the task was "internal retrieval plumbing" and skipped the API surface entirely.

**Fix:** folded into B1 (decision-driven injection). Fresh run_023 does not reproduce this pattern.
