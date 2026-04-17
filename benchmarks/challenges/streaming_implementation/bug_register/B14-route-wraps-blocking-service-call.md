# B14 — Route wraps blocking `service.query()` in `StreamingResponse`

**Status:** open
**Impact:** 6/10
**Opened:** 2026-04-17
**Source:** Tier-2 Sonnet scoring of run_021 — ~7/30 plans

## Symptom

Route artifact shape:

```python
@router.post("/stream")
async def stream_query(request: QueryRequest):
    async def event_generator():
        answer = service.query(...)           # blocking, full Answer
        yield json.dumps({"text": answer.text})   # single yield
    return StreamingResponse(event_generator())
```

The route claims to stream, uses `StreamingResponse`, references the right
request fields, but **calls the blocking `service.query()` method** and
then yields the entire answer as a single chunk. Client sees one big
response after the full pipeline runs — zero streaming benefit.

This is route-level A4 (blocking + split) — the sibling to **B9** at a
different layer. Same anti-pattern: streaming scaffolding wrapped around
a blocking call.

## Why Tier-1 misses this

- `StreamingResponse` is present — Tier-1's R1/R2 detection is satisfied.
- `service.query()` is a real method that exists in the codebase — no
  fabrication to flag.
- The single `yield` is enough to satisfy "has yield" check.

## Generalization

Same invariant family as B9, one layer up:

> **When an artifact wraps a call to an object X in a streaming construct
> (StreamingResponse, async generator, SSE emitter), and X exposes both a
> blocking and a streaming variant of the method, the wrapper must call the
> streaming variant.**

So if `service.query()` (blocking) and `service.stream_query()` (streaming)
both exist (or both are defined in the artifact set), a route that wraps
output in `StreamingResponse` must call `service.stream_query()`.

This is B9 generalised across any two layers.

## Scope of the class

- Routes wrapping service calls.
- Services wrapping engine calls.
- Engines wrapping synthesizer calls (B9).
- Any N-layer orchestration where the model adds streaming scaffolding at
  one layer without threading it through.

## Fix direction

A single invariant check that operates on the whole artifact set
(CLAUDE.md rule 11 — set-level checks):

For each artifact that contains a streaming construct
(`StreamingResponse(...)`, `async generator`, `yield from` in a
generator function), walk its calls. For each call on an object typed
to a class with both a blocking and a streaming variant (detected by
name pattern match: `foo` + `stream_foo` or `foo` + `foo_stream` on
the same class, in the codebase or in the artifact set), verify the
streaming variant is called.

## Acceptance

- 0/5 replay plans have routes calling `service.query()` inside a
  StreamingResponse.
- Closure report lists concrete violations when they occur.
- Subsumes B9 — merge the two fixes if feasible.
