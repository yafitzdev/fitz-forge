# B8 — Scorer type agreement fp

**Status:** open
**Impact:** 3/10

**Evidence:** Run 020 plan 02 — scorer reports `Streaming methods have incompatible return types` because `core/answer.py:stream_query -> Generator[AnswerChunk, None, Answer]` and `api/routes/query.py:stream_query -> Generator[str, None, None]`. These are on DIFFERENT classes at different layers (core streamer vs API wrapper) — the names match but the methods aren't the same concept.

**Generalization:** type agreement check should group by owner class + method name, not just method name. Methods in different files/classes sharing a name are not necessarily the same concept.

**Cost:** plan 02 cons 20 → 10, total 100 → 89.3. Single-plan impact only right now.
