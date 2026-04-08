# V2-F7: Missing Required File

**Occurrence:** 2/10 plans (run 83)
**Impact:** ~7 pts (completeness drops from 30/30 to 15.5/30)

## What Happens

A plan doesn't produce an artifact for one of the two required files (engine.py or routes/query.py). The taxonomy defines these as required because streaming needs both the engine-level streaming method AND the API endpoint to expose it.

## Affected Plans

- plan_07: missing engine.py (only has routes + service artifacts)
- plan_10: missing routes/query.py (only has engine + service artifacts)

## Why It Happens

The decision decomposition stage doesn't always produce decisions that cover both required files. If the decomposition focuses on one layer (e.g., "how does the engine stream?") without creating decisions for the other layer (e.g., "how does the API expose the stream?"), no artifact gets generated for the missing file.

## Potential Fixes

1. **Completeness check in decomposition**: after decisions are generated, verify that required files (from taxonomy) are covered by at least one decision's `relevant_files`. If not, inject a decision.
2. **Post-synthesis check**: if required files are missing from artifacts, generate them in a follow-up pass.
3. **This is rare** (2/10) — may not warrant a dedicated fix yet. Monitor over more runs.
