# V2-F1: Engine.py Parse Failure

**Occurrence:** 1/10 plans (run 89), was 3/10 (run 83)
**Impact:** ~3 pts per occurrence (size-weighted)

## What Happens

The surgical rewrite produces engine.py artifacts that are indented method bodies (4-space indent, meant to be inside a class). These aren't standalone-parseable Python. The V2 scorer's parse recovery (dedent) fixes most cases, but some fail due to:

1. **Truncated content** — the LLM's output gets cut off mid-string-literal (e.g., trailing `"` on last line)
2. **Mixed indentation** — some lines dedent correctly, others don't

## Status

Mitigated by the LLM quality layer (run 89). The `generate()` function caps max_tokens to context budget and retries on truncation. Down from 3/10 to 1/10.

Remaining occurrences are from truncation retries that also fail (5/6 retries produced truncated output again in run 89).

## Potential Further Fixes

1. **Pipeline fix**: surgical rewrite wraps output in `class _Engine:` stub to make it parseable
2. **Generation fix**: "continue" prompt for truncation retry instead of repeating the same prompt
3. **Scorer fix**: more aggressive parse recovery — strip trailing garbage lines and retry
