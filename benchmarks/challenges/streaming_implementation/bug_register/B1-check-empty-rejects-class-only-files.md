# B1 — Check empty rejects class only files

**Status:** resolved
**Impact:** 7/10
**Closed:** 2026-04-16

**Evidence:** Run 19 replay 2 — both `schemas.py` and `core/answer.py` exhausted 3 attempts with `empty: Content has no function or method definitions`. The model produces valid Pydantic/dataclass classes with annotated fields — the original check required `def` to be present anywhere.

**Generalization:** applies to every file whose purpose is *data model* (schemas, DTOs, events). These are valid Python but contain no `def`.

**Fix:** `validate.py:_check_empty` + `_is_data_class`. Accepts BaseModel, dataclass, Enum, TypedDict, and any class with annotated fields.
