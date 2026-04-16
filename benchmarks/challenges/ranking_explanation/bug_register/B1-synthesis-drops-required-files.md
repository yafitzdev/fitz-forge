# B1 — Synthesis drops required files

**Status:** resolved
**Impact:** 10/10
**Closed:** 2026-04-16

**Evidence:** All 10 baseline plans had decisions d6 (ranker.py) and d7 (reranker.py) with concrete evidence. Resolution produced valid decisions. But synthesis reasoning selected only strategy-level files and API-layer files. Ranker, reranker, and engine were never generated as artifacts.

**Root cause:** V2-F7 injection required 2+ evidence citations per file. ranker.py and reranker.py each appeared in only 1 decision's evidence, so injection didn't fire.

**Generalization:** the invariant is "every resolved decision targeting a specific file should produce an artifact for that file."

**Fix:** `synthesis.py:_enforce_decision_coverage` criterion 2. Files appearing as evidence sources in resolved decisions are now injected even if only referenced by 1 decision.

**Progress:** fresh run_023 avg 97.08, +28.23 from baseline 68.85.
