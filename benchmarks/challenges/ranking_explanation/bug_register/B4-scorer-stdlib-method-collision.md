# B4 — Scorer stdlib method collision

**Status:** resolved
**Impact:** 7/10
**Closed:** 2026-04-16

**Evidence:** `synthesizer.py calls provenance.append()` matched against `provenance.py` (list method, not a provenance method). `query.py calls router.post()` matched against `router.py` (FastAPI APIRouter method). Variable names collide with file names.

**Generalization:** method_name_agreement matches variable-name → file-basename, producing false positives when common names appear in both contexts.

**Fix:** `benchmarks/eval_v2_deterministic.py` — skip method_name_agreement when the called method is a stdlib/framework method (append, extend, post, get, put, delete, etc.).

**Progress:** ranking replay 75.5 → 94.4 after this fix alone.
