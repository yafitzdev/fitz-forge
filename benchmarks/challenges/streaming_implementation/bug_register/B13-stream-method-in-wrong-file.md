# B13 — `stream_*` method placed in `core/answer.py` with `NotImplementedError`

**Status:** open
**Impact:** 4/10
**Opened:** 2026-04-17
**Source:** Tier-2 Sonnet scoring of run_021 — ~5/30 plans

## Symptom

The plan's artifact set includes a file `fitz_sage/core/answer.py` whose body
is a single method stub:

```python
def stream_query(self, ...):
    raise NotImplementedError("...")
```

`answer.py` is a `@dataclass` data model file. The streaming method belongs
on the engine (`FitzKragEngine`), or on the SDK (`fitz` class). Placing it
on the `Answer` dataclass is semantically wrong and the method is dead code
(no caller ever reaches it; the signature is also wrong for an Answer
instance).

## Why Tier-1 misses this

- The artifact parses. `NotImplementedError` is caught by a specific check
  (`B8` flagged related patterns as resolved), but only when it's the sole
  behaviour of the method. The placement-in-wrong-file issue is orthogonal.
- No check verifies that a method added to class X is *semantically
  appropriate* for X.

## Generalization

Invariant: **when the model adds a method to an existing class defined on
disk, the class's existing method set should tell us its role (data model
vs service vs value object). Adding a service-layer method to a data-model
class is a semantic mismatch we can heuristically detect.**

Heuristic (deterministic, codebase-agnostic):
- If the class is a `@dataclass` / frozen Pydantic BaseModel / has only
  typed attributes + `__init__`, it is a data model.
- Methods whose body reaches out (makes service calls, queries databases,
  calls LLMs) don't belong on data models.

Strict version: if the method body has zero references to `self.<attr>`
for any attr of the class, and the class is a data model, the method
probably doesn't belong on this class.

## Scope of the class

- Any task where the model creates stub methods in the wrong file.
- Independent of streaming; will recur on any "where does this new method
  live?" decision.

## Fix direction

Add a per-artifact validation step: "method-class role compatibility."
- Classify the target class: data model vs service/orchestrator.
- If a method's body shows service-layer behaviour (no `self.<attr>`
  access, calls external services), warn when the class is a data model.
- Targeted regeneration feedback: "Answer is a dataclass — this streaming
  method belongs on FitzKragEngine (see engines/fitz_krag/engine.py)."

## Acceptance

- 0/5 replay plans place `stream_*` methods in `core/answer.py`.
- When `core/answer.py` is modified, the change is limited to adding
  fields (e.g. `StreamChunk` dataclass) — not behavioural methods.
