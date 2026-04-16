# R3 — Typevar false positives

**Status:** resolved
**Impact:** 4/10
**Closed:** 2026-04-16

**Evidence:** After R1 generalized the class-fabrication check to every type position, `T = TypeVar("T")` bindings used as `def handler(fn: T) -> T` started triggering "missing class T" violations. Single-letter uppercase names are conventionally TypeVars, not classes.

**Fix:** `closure.py:_find_module_typevars` detects `X = TypeVar(...)` / `ParamSpec(...)` / `TypeVarTuple(...)` at any scope and passes them through to the reference collector's `local_skip` set. `_iter_annotation_class_names` additionally skips single-letter uppercase names as a fallback.
