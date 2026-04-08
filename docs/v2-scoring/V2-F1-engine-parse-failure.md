# V2-F1: Engine.py Parse Failure

**Occurrence:** 3/10 plans (run 83)
**Impact:** ~10 pts (unparseable = all fabrication checks fail = artifact scores ~35/100, dominates size-weighted mean)

## What Happens

The surgical rewrite produces engine.py artifacts that are indented method bodies (4-space indent, meant to be inside a class). These aren't standalone-parseable Python. The V2 scorer's parse recovery (dedent) fixes most cases, but some fail due to:

1. **Truncated content** — the LLM's output gets cut off mid-string-literal (e.g., trailing `"` on last line)
2. **Mixed indentation** — some lines dedent correctly, others don't

## Example

```python
    def answer_stream(self, query: Query, ...) -> Iterator[str]:
        """Stream token-by-token..."""
        ...
        clear_query_context()"   # ← truncated string literal
```

Dedent recovers the body, but the trailing `"` causes `unterminated string literal` error.

## Affected Plans

Run 83: plan_01 (332 lines), plan_02 (339 lines), plan_03 (361 lines)

## Potential Fixes

1. **Pipeline fix**: surgical rewrite should wrap output in `class _Engine:` stub to make it parseable, or strip trailing garbage after generation
2. **Scorer fix**: more aggressive parse recovery — strip last N lines on unterminated string literal and retry
3. **Generation fix**: set explicit `stop` tokens or `max_tokens` to prevent truncation

## Score Impact

When engine.py (300+ lines) fails to parse, the size-weighted quality score tanks because:
- engine.py is 50-70% of total artifact weight
- Unparseable → all fabrication checks count as failed → ~35/100 artifact score
- A single unparseable engine.py can drop the plan score by 15-20 pts
