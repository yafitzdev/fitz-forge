# B2 — Engine self method fabrication

**Status:** resolved
**Impact:** 9/10
**Closed:** 2026-04-16

**Evidence:** Run 19 replay 2 — engine.py attempt 1 fabricated `self._execute_pipeline()`; attempt 2 fabricated `self._run_pipeline()`; attempt 3 produced unparseable output. Retry feedback is error messages only — the model loops because it doesn't see the real set of self methods.

**Generalization:** every surgical artifact retry on a method-rich target class has this shape.

**Fix:** `context.py:_extract_target_self_methods` + `strategy.py:_surgical_grounding_block`. The target class's real method signatures are now injected as a "METHODS AVAILABLE ON self" block with an explicit "do NOT invent new helper names" rule in both surgical and new-code prompts.

Cycle 2 replay: engine.py succeeds on attempt 1 (16213 chars). **Plan 01 87.9 → 100.0** after this cycle + B1 + R1 stack.
