# B4 — Grounding missing class fp

**Status:** resolved
**Impact:** 7/10
**Closed:** 2026-04-16

**Evidence:** Grounding reported `missing_class: ChatResponse / ChatMessage / Chunk / FitzService / SourceInfo / Answer` on artifacts that reference them by name. Those classes all exist in fitz-sage — the grounding `StructuralIndexLookup` wasn't seeing them even when `source_dir` was provided.

**Root cause:** the structural index passed to grounding was built from the agent's retrieval output (50-ish files) rather than a full codebase scan. Anything outside the retrieval subset looked "missing."

**Fix:** `grounding/check.py:check_all_artifacts` + `grounding/llm.py:validate_grounding` + `orchestrator.py`. `source_dir` threaded through so grounding calls `StructuralIndexLookup.augment_from_source_dir` with the full codebase.
