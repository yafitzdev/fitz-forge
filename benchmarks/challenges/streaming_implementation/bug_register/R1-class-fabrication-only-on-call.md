# R1 — Class fabrication only on call

**Status:** resolved
**Impact:** 8/10
**Closed:** 2026-04-16

**Evidence:** Closure's existence check originally only emitted a class reference when `ClassName(...)` appeared as a Call node. Fabricated classes appearing in parameter annotations, return annotations, variable annotations, raise, except, isinstance, or cast slipped through entirely.

**Generalization:** every type position that names a class must participate in the existence check — not just instantiation.

**Fix:** `closure.py:_iter_annotation_class_names` walks annotation subtrees. `_emit_annotation_types` emits one class reference per capitalized Name. `_find_module_typevars` skips TypeVar bindings. `visit_Raise` / `visit_ExceptHandler` cover their respective positions. `visit_Call` special-cases `isinstance`/`issubclass`/`cast` arguments.
