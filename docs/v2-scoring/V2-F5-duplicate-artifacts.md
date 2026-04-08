# V2-F5: Duplicate Artifacts — FIXED

**Status:** Fixed in run 84
**Fix:** Deterministic dedup in `_build_artifacts_per_file` (synthesis.py)

## What Was Happening

Per-function decomposition (F25) generated one artifact per route handler for `routes/query.py`, producing 2-3 copies of the same file. The synthesis stage didn't merge them.

## Fix

After all artifacts are generated, group by normalized filename:
- Identical content: keep one, drop rest
- Different content: keep the longest

## Result

- Run 83: 6/10 plans had duplicates, -13.2 pts average impact
- Run 84: 0/7 plans have duplicates
