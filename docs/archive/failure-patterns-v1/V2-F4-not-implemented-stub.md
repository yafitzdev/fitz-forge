# V2-F4: NotImplementedError Stub

**Occurrence:** 1/10 plans (run 83)
**Impact:** ~3 pts (7.5% weight in artifact score)

## What Happens

An artifact contains `raise NotImplementedError` instead of a real implementation. The model gave up on generating the actual code and left a placeholder.

## Affected Plans

Run 83: plan_01 (error_handlers.py)

## Why It Happens

The artifact is for a file the model doesn't have enough context about. Error handlers are tangential to the streaming task — the model was told to create one but didn't know how to implement it.

## Potential Fixes

1. **Pipeline fix**: don't generate artifacts for files where the model has no reference implementation
2. **Decision fix**: decomposition should not create decisions for tangential files
3. **Scorer**: already detected — costs 7.5% of that artifact's score
