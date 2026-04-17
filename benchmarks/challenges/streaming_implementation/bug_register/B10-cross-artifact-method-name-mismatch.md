# B10 — Service calls fabricated engine method name across artifact boundary

**Status:** open
**Impact:** 7/10
**Opened:** 2026-04-17
**Source:** Tier-2 Sonnet scoring of run_021 — ~12/30 plans

## Symptom

Cross-artifact method-name drift. Within the same plan:

- `engine.py` artifact defines `stream_query(self, ...)` (or `answer_stream`
  or `stream_answer`, picked by the model from decision to decision).
- `fitz_service.py` artifact in the *same plan* calls
  `engine_instance.stream_answer(...)` when the engine defines `stream_query`
  — or vice versa.

Runtime effect: `AttributeError` on first streaming request.

## Why Tier-1 misses this

- Each artifact is parseable on its own.
- The closure existence check (`B4` family) does flag missing cross-file
  symbols, but is not consistent about flagging methods on locally-owned
  service types. Specifically, if the service's `engine_instance` is typed
  via service-locator return-type inference, the call may be flagged; if it's
  a constructor-injected `self._engine` of unknown type, it isn't.

## Generalization

Invariant: **every method name emitted by one artifact as a call on an object
whose type is known from another sibling artifact must appear as a defined
method in that type's artifact — including new methods introduced in this
plan.**

This is a variant of the existing closure "existence" invariant but extended
to cross-artifact NEW methods. The current closure check knows about the
codebase's existing methods but may not cross-reference newly-added methods
across sibling artifacts.

Related to but not the same as B2 (self-method fabrication within one file).

## Scope of the class

- Any multi-layer plan where a service/orchestrator/SDK calls into a
  plan-added engine/provider method.
- Will appear in both directions: service→engine, SDK→service, route→service.

## Fix direction

1. Extend the closure existence check to build a method-name set from ALL
   artifacts in the set (not just from disk source + type-tracked variables).
2. When an artifact calls `instance.some_method()`, and `instance`'s type
   is defined in a sibling artifact, and `some_method` is not in that
   sibling's method set, emit a closure violation with concrete suggestion
   (list the sibling's actual methods).

## Acceptance

- On replay, 0/5 plans show this specific cross-artifact drift.
- Closure report lists concrete violations when they occur (so fixer loop
  can target them).
