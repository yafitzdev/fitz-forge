# V2-F8: Fabricated Types

**Occurrence:** 1/10 plans with significant fabrication (run 89), was 5/7 (run 84)
**Impact:** -15 pts on affected plan (concentrated — plan_03 had 9 fabs, others 0-1)

## What Happens

The model invents new classes that don't exist in the codebase. Three sub-patterns:

### F8a: Fabricated method calls on instrumentation/core files

The model creates artifacts for files like `core/instrumentation.py` that don't exist in the codebase, filling them with fabricated method calls.

**Run 89 data:** plan_03 created `core/instrumentation.py` (51 lines, 6 fabrications) + engine.py with 3 fabricated calls = 9 total. This is an outlier — the other 9 plans had 0-1 fabs.

### F8b: Fabricated provider subclasses

The model creates `OpenAIStreamingChat`, `AnthropicStreamingChat` etc. — new subclasses for each provider. The existing providers already have `chat_stream()`.

**Run 89:** Not seen. Fixed by decomp scorer's ref_complete criterion.

### F8c: Fabricated request DTOs

The model creates request DTO classes that don't exist in the codebase.

**Run 89:** Not seen.

## Score Impact

Each fabricated class costs heavily under the combined fabrication weight:
- 1 class: score x 0.7 = -15 pts
- 3 classes: score x 0.2 = -40 pts
- 5+ classes: score x 0.0 = -50 pts

## Status

Mostly resolved. The decomp scorer's ref_complete criterion prevents the systematic fabrication patterns (F8b, F8c). F8a appears as rare outliers (1/10 plans) — the model occasionally creates files that aren't in the codebase. Not worth optimizing further at current frequency.
