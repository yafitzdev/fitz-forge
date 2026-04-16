# B1 — Zero artifact plans

**Status:** resolved
**Impact:** 10/10
**Closed:** 2026-04-16

**Evidence:** Baseline run_024 plans 06, 09 had 8 decomposition decisions, 8 resolutions, but `needed_artifacts: 0`. Synthesis reasoning (9K chars) didn't include the JSON-structured file list. Fallback to template extraction also failed.

**Root cause:** LLM output formatting variance — the model writes a long prose plan but omits the structured `needed_artifacts` section.

**Generalization:** any task where synthesis reasoning omits the artifact list. Language-agnostic — same bug could hit Python tasks.

**Fix:** `synthesis.py` — evidence-source injection now runs BEFORE the template fallback. When `needed_artifacts` is empty, resolved decisions still provide the file list. Run_027: 0 zero-artifact plans.
