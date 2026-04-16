# V2-F3: Streaming File Missing Yield

**Occurrence:** 2/10 plans (run 83)
**Impact:** ~5 pts (10% weight in artifact score, plus likely indicates wrong architecture pattern)

## What Happens

An engine.py artifact for a streaming task contains no `yield` keyword. This means the artifact returns a blocking `Answer` object instead of streaming tokens — defeating the purpose of the task.

## Affected Plans

Run 83: plan_08 (337 lines), plan_09 (330 lines)

Both have parseable engine.py artifacts that produce `Answer` objects instead of using `yield`. These plans chose an A4/A5 architecture pattern (blocking + split or NotImplementedError).

## Relationship to Other Patterns

This is a **quality signal**, not just a syntax issue. Missing yield usually means:
- The model didn't understand the streaming requirement
- The surgical rewrite's pipeline constraint was followed but the final step wasn't converted to streaming
- The "only the final output step may change" instruction wasn't specific enough

## Potential Fixes

1. **Prompt fix**: surgical rewrite instruction should explicitly say "replace `return Answer(...)` with `yield` tokens"
2. **Pipeline fix**: post-generation check for yield in streaming artifacts, retry if missing
3. **Scorer**: already detected — this pattern correctly costs points
