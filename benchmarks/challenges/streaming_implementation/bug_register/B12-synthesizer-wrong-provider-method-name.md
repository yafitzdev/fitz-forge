# B12 — Synthesizer calls `chat.stream_chat()` or `chat.stream()` — real method is `chat_stream`

**Status:** open
**Impact:** 5/10
**Opened:** 2026-04-17
**Source:** Tier-2 Sonnet scoring of run_021 — ~6/30 plans

## Symptom

Synthesizer artifact shape:

```python
def stream_generate(self, ...):
    for chunk in self._chat.stream_chat(messages):   # or .stream(...)
        yield chunk
```

The existing `StreamingChatProvider` protocol in `fitz_sage/llm/providers/
base.py` defines `chat_stream()`. `stream_chat` / `stream` do not exist.
Runtime: `AttributeError`.

Often the model doubles down and adds a *new* `stream_chat` or `stream`
method to `ChatProvider` (the wrong protocol) in a separate artifact to
"resolve" the reference — introducing a second broken pattern (protocol
widening, B6 family).

## Why Tier-1 misses this

- If the artifact set includes a matching (fabricated) `stream_chat` method
  on `ChatProvider`, the closure existence check resolves — same symbol
  present somewhere.
- Partially covered by **B6** (protocol widening) which was resolved for
  one specific shape but evidently not all.

## Generalization

Invariant (already expressed in B6): **when the model adds a method to a
Protocol, that method name must not collide with an adjacent protocol in
the same module that already defines an equivalent streaming method.**

Extension of B6: also check that when a method in a sibling artifact
references `self._x.foo()` where `self._x` is typed as Protocol P, `foo`
must be defined on P in the *codebase* — not only in the plan's new
artifact. If the plan adds `foo` to P *and* references `P.foo` elsewhere,
that's acceptable only if the new `foo` doesn't duplicate an existing
streaming method on a sibling protocol.

## Scope of the class

- Any plan that introduces a streaming variant by adding a method to the
  wrong protocol.
- Reaches beyond streaming: same pattern can appear when adding any new
  async/streaming method to a base protocol.

## Fix direction

Strengthen B6's check:
1. When a new method is added to a Protocol in an artifact, scan sibling
   artifacts for Protocols in the same module.
2. If any sibling Protocol already defines a method with the same semantic
   role (by name pattern or parameter shape), flag as protocol confusion.
3. Offer targeted regeneration feedback: "StreamingChatProvider.chat_stream
   already defines the streaming interface — call it directly, don't add
   a new method to ChatProvider."

## Acceptance

- 0/5 replay plans introduce new `stream_chat` / `stream` methods on
  `ChatProvider`.
- When the synthesizer calls `self._chat.chat_stream(...)`, `self._chat`
  is typed as `StreamingChatProvider` (or suitable union).
