# B7 — Enum value flagged missing

**Status:** resolved
**Impact:** 6/10
**Closed:** 2026-04-16

**Evidence:** Run 20 plans 01, 02 — closure reports `AnswerMode.value — missing field` for `answer_mode.value`. `AnswerMode` is an Enum (`class AnswerMode(str, Enum)`), and `.value` is a standard Enum attribute inherited from `enum.Enum`.

**Generalization:** every Enum/Flag/IntEnum/StrEnum/IntFlag subclass has `.value` and `.name`. Stdlib-standard members, accepted automatically.

**Fix:** `closure.py:_ENUM_STANDARD_ATTRS` + `_is_enum_class`. Enum subclasses walking to an `Enum`/`Flag`/`IntEnum`/`StrEnum`/`IntFlag`/`ReprEnum` base now accept standard Enum attributes.
