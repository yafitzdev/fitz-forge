# B17 — `_collect_yielded_names` misses identifiers inside complex yield expressions

**Status:** resolved
**Impact:** 9/10 (final blocker for B9 firing on production patterns)
**Opened:** 2026-04-18
**Closed:** 2026-04-18

**Fix:** Walk the entire yield-expression subtree for identifiers,
not just `yield <bare_name>` / `yield from <bare_name>`. Models
wrap streamed data in many shapes (`yield {"data": x.__dict__}`,
`yield Chunk(text=x)`, etc.); the narrow check missed every one.

## Symptom

In `closure.py:_collect_yielded_names`, the only patterns that
contributed to the yielded-names set were:
- `yield <identifier>` (bare name as direct child of yield)
- `for x in y: yield ...` (added `y`)

Production engine artifacts often yield wrapped objects:

```python
answer = self._synthesizer.generate(...)   # blocking call
yield {"type": "answer", "data": answer.__dict__}   # never bare `yield answer`
```

`_call_is_data_producing` checks: "the call's result is assigned to
`answer`; is `answer` in `yielded_names`?" → **No**, because the yield
is a dict literal, not `yield answer`. So the call is treated as a
helper, not a streaming data source. B9 streaming-sibling check is
suppressed by the false-positive guard rule 1.

## Evidence

Replay of B9+B15+B16 still produced a broken plan. Direct repro:

- engine.py: `async def stream_query() -> AsyncGenerator[dict, None]`
  with 8 `yield {"type": ..., "data": answer.__dict__}` statements.
- self_attrs correctly resolves `_synthesizer = CodeSynthesizer` (B16 ✓).
- sibling_provides correctly registers `CodeSynthesizer.stream_query`
  (B15 ✓).
- Direct call to `_collect_yielded_names`: returned `set()` (no names).
- `_call_is_data_producing` for `answer = self._synthesizer.generate()`
  returned False because `answer` not in yielded_names.
- B9 streaming-sibling violations emitted: 0.

After fix (walk yield-subtree for identifiers):
- `_collect_yielded_names` returns `{answer, gap_context, sanitized,
  cached, ...}` — all names referenced anywhere in any yield expression.
- B9 violations emitted: **2** (engine.py:315 — `_synthesizer.generate`,
  engine.py:191 — `_synthesizer._build_abstain_message`).

## Generalization

Invariant: **a value is "data-producing for the yield path" if its
identifier is referenced anywhere inside any yield expression in the
function body — not just when the yield is a bare name.**

This is the only sound interpretation given how generators are written
in practice: structured chunks (`yield {...}`), wrapped objects
(`yield Chunk(text=x)`), etc. are the dominant shapes; bare yields are
rare in production streaming code.

Side note: the broader rule introduces a small false-positive risk
(`count = call(); yield {"count": count}` — call result yielded
indirectly even if it was a side-effect helper). Acceptable per the
B9 spec — false positives surface as feedback to the model, which
can decide to keep the helper call.

## Scope of the class

Affects every closure invariant that uses yielded-names to detect
"is this call data-producing":
- B9 streaming-sibling (this case)
- Any future invariant that needs "does this value flow into a yield?"

## Acceptance

- B9 broader check fires on the production engine.py + synthesizer.py
  artifact pair from streaming_implementation replay (verified).
- 20 streaming-sibling tests still pass.
- No regression elsewhere.

## Relationship to B9 / B15 / B16

The full chain B9 → B15 → B16 → B17 is now closed. With B17 in place,
the streaming-sibling check fires on the dominant real-world variant
without further fixes. Replay-validation expected to show model
regenerating engine.py with `self._synthesizer.stream_query(...)`
instead of `.generate(...)`.
