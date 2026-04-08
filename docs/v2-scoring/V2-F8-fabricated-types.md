# V2-F8: Fabricated Types

**Occurrence:** 5/7 plans (run 84), 1/10 plans (run 83)
**Impact:** -10 to -25 pts per plan (50% weight, scaled by count)

## What Happens

The model invents new classes that don't exist in the codebase. Three sub-patterns:

### F8a: Streaming chunk types (most common)

The model yields `StreamChunk(type="answer", text=token)` instead of `yield token` (raw string). It invents typed chunk objects because its training data shows OpenAI/Anthropic SDKs using typed chunks (`ChatCompletionChunk`, `MessageStreamEvent`).

**Examples:** `StreamChunk`, `ChatStreamChunk`, `StreamingChatDelta`

**Root cause (5-why):**
1. Why does it yield StreamChunk? Because it thinks streaming needs typed objects.
2. Why does it think that? Training data — OpenAI/Anthropic SDKs use typed chunks.
3. Why doesn't the surgical prompt prevent this? The reference `answer()` returns `Answer(text=..., sources=...)`. The model generalizes: "blocking returns Answer, streaming should return StreamChunk."
4. Why doesn't it see the existing streaming pattern? `chat_stream()` returns `Iterator[str]` but the surgical prompt shows only the blocking `answer()` method, not `chat_stream()`.
5. **Root cause:** The surgical prompt lacks an example of the codebase's raw-string streaming pattern.

**Fix:** Add to surgical prompt: "This codebase streams raw `str` tokens via `Iterator[str]`. Do NOT create chunk/delta types. Yield plain strings."

### F8b: Streaming provider subclasses (factory.py)

The model creates `OpenAIStreamingChat`, `AnthropicStreamingChat` etc. — new subclasses for each provider. The existing providers (`OpenAIChat`, `AnthropicChat`) already have `chat_stream()`.

**Root cause:** The decisions reference `factory.py` for "wiring streaming providers" but the model doesn't know existing providers already support streaming. The synthesis reasoning doesn't mention `chat_stream()` on existing providers.

**Fix:** Decision-level: decomposition should check if provider classes already have streaming methods before creating decisions to modify the factory. Or: don't generate factory.py artifacts at all for this task (the factory doesn't need changes).

### F8c: Request/response DTOs

The model creates `AnswerRequest(question=..., collection=...)` — a request DTO for the service layer. The existing service uses plain parameters.

**Root cause:** The model's training shows DTOs as best practice for service methods. The codebase uses `service.query(question, collection)` not `service.query(AnswerRequest(...))`.

**Fix:** Lower priority. Only 1 plan affected. The existing service interface is in the structural index — the model should follow it.

## Score Impact

Each fabricated class costs heavily under the combined fabrication weight:
- 1 class: score × 0.7 = -15 pts
- 3 classes: score × 0.2 = -40 pts  
- 5+ classes: score × 0.0 = -50 pts

Plan_04 (run 84) has 5 `StreamChunk` fabrications → 0/50 artifact quality on those artifacts.

## Priority

**F8a is the highest priority** — it's the most common (5 plans) and has a clear fix (add streaming pattern to surgical prompt). F8b and F8c are rarer and harder to fix (require decision-level changes).
