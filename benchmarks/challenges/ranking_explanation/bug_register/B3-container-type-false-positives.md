# B3 — Container type false positives

**Status:** resolved
**Impact:** 7/10
**Closed:** 2026-04-16

**Evidence:** `var: list[Address]` bound `var` to type `Address`, then `var.extend(...)` was flagged as `Address.extend` — a missing method. Container element types aren't the variable's type.

**Generalization:** `list[X]`, `dict[K,V]`, `set[X]`, etc. — the variable is a container, not an element. Element-type binding is only correct for transparent wrappers like `Optional[X]` and `Union[X,Y]`.

**Fix:** `inference.py:extract_type_name` + `_CONTAINER_TYPES`. Container generics return the container name (which falls through `_SKIP_NAMES`). Transparent wrappers still drill into the inner type.
