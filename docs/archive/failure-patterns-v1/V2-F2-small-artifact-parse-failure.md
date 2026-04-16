# V2-F2: Small Artifact Parse Failure

**Occurrence:** 6/10 plans (run 89), was 6/10 (run 83)
**Impact:** ~0 pts (small artifacts have low weight in size-weighted mean)

## What Happens

Non-engine artifacts (schemas.py, sdk/fitz.py, dependencies.py) are generated as code fragments that don't parse as standalone Python. These are typically 6-36 lines — too short for parse recovery to help, and too small to meaningfully affect the size-weighted score.

## Top Offenders (run 89)

| File | Plans | Lines |
|------|-------|-------|
| schemas.py | 6/10 | 6-25 |
| sdk/fitz.py | 4/10 | 36 |
| routes/query.py | 3/10 | 39-73 |
| dependencies.py | 1/10 | 64 |
| services.py | 1/10 | 29 |

## Status

**Won't fix** — the size-weighted scoring correctly assigns near-zero impact. A 15-line schemas.py at 90/100 barely moves the score vs a 350-line engine.py at 100/100.
