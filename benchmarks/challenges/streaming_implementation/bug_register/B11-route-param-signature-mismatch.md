# B11 — Route function signature takes positional params but body references `request.*`

**Status:** superseded by semantic-review gate
**Impact:** 6/10
**Opened:** 2026-04-17
**Superseded:** 2026-04-18
**Source:** Tier-2 Sonnet scoring of run_021 — ~8/30 plans

**Supersession note (2026-04-18):** The per-artifact unbound-name check
(`_check_unbound_names` in `validate.py`) was removed. The signature/body
mismatch it targeted is a contradiction between what the method signature
declares and what the body references — exactly the class of bug the
LLM semantic-review gate reports as an `actual: "body references
request.X but signature is flat positional"` discrepancy. The gate
routes it back through `generate_artifact` with natural-language
feedback, so the repair pathway is preserved; the detection moves from
a tree-sitter scope walker (~580 LOC) to LLM review (one prompt).

## Symptom

Route artifact shape:

```python
async def stream_query(question: str, source: str = None,
                       collection: str = "default", top_k: int = 5,
                       conversation_history: list = None):
    # body references `request.source`, `request.question`,
    # `request.collection`, `request.top_k`, `request.conversation_history`
```

The function signature takes flat positional params, but the body uses a
`request` variable that doesn't exist in scope. Runtime: `NameError`.

## Why Tier-1 misses this

- The artifact parses. Variable resolution at parse time is tolerant of
  unbound names.
- The `request.*` accesses don't map to any known fabrication pattern —
  `request` is a common FastAPI-ish name so the closure check would need
  scope-awareness to know that `request` isn't defined.

## Generalization

Invariant: **every name accessed in an artifact's method body must resolve
to one of: a parameter of the method, a class attribute (`self.*`), a
module-level import, a sibling artifact export, or a builtin.**

Local-scope unbound name detection. Tree-sitter + a simple scope walker
would catch this deterministically and language-agnostically.

## Scope of the class

- Any generated code that mixes "single request object" and "flat params"
  conventions.
- Will appear in routes, CLI commands, SDK methods, tests.

## Fix direction

Add a scope-resolution check to per-artifact validation (next to the
existing "no fabrication" check). For each method body: collect bound
names (params, assignments, imports, `self.*`, closures). For each
`Name` / `Attribute` access on a bare identifier: verify it's bound
or imported or a builtin. Flag unresolved names with suggestion
("did you mean one of: {params}?").

## Acceptance

- 0/5 replay plans show unbound `request.*` access.
- Regression test: a crafted artifact with unbound `request` access fails
  validation and triggers regeneration.
