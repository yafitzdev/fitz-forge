# V2-F2: Small Artifact Parse Failure

**Occurrence:** 6/10 plans (run 83)
**Impact:** ~3 pts per artifact (small artifacts have low weight in size-weighted mean)

## What Happens

Non-engine artifacts (schemas.py, types.py, dependencies.py) are generated as code fragments that don't parse as standalone Python. These are typically 7-19 lines — too short for the indentation-based recovery to help, and too small to contain structural errors.

## Common Causes

1. **Missing imports** — fragment starts with `class StreamingResponse(BaseModel):` without `from pydantic import BaseModel`
2. **Incomplete class** — only field definitions, no closing context
3. **Decorator without function** — `@router.post(...)` followed by truncated handler

## Affected Files (run 83)

| File | Plans | Lines |
|------|-------|-------|
| schemas.py | 4/10 | 10-19 |
| types.py | 1/10 | 7 |
| routes/query.py | 2/10 | 56-79 |

## Why Less Impactful Than V2-F1

These artifacts are 7-79 lines vs engine.py's 300+. In the size-weighted mean, a 15-line schemas.py at 35/100 barely moves the needle compared to a 350-line engine.py at 35/100.

## Potential Fixes

1. **Pipeline fix**: artifact generation prompt should require complete, importable Python
2. **Scorer fix**: for fragments <30 lines, attempt wrapping in module boilerplate before failing
3. **Pipeline fix**: post-generation syntax check with retry (already exists for grounding violations — extend to parse errors)
