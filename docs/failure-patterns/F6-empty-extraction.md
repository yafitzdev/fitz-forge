# F6: Empty Extraction Fields

## Problem
Field group extraction sometimes returns empty arrays for sections that should have content. Most common: `phases: []` (empty roadmap), `approaches: []` (empty architecture), `components: []` (empty design).

## Impact
- Plan has hollow sections with no actionable content
- Sonnet scorer penalizes heavily on implementability and scope calibration

## Occurrence Rate
Was ~10% when JSON regex bug existed (corrupting [d1] citations inside strings).
**Current baseline:** 0/150 empty across 3 critical groups (50 runs each for phases, approaches, components).
The JSON regex fix from a prior session appears to have resolved the root cause.

## Root Cause
Two causes:
1. JSON parsing failure on the extraction output (model produces valid content but parser chokes) — fixed by the JSON regex fix
2. Model genuinely produces empty arrays — rare, happens when reasoning doesn't cover that section well

## Fix (IMPLEMENTED)
Safety net retry: `_extract_field_group()` accepts `retry_if_empty` parameter. If a critical field (`phases`, `approaches`, `components`) returns empty after extraction, retry that specific group once. Cheap (one LLM call, only triggered on failure) and safe (worst case: empty again, fall back to Pydantic defaults).

Wired up for: approaches (arch), components (design), phases (roadmap).

Commit: pending

## Test Data
- Harness: `benchmarks/test_f6_empty.py`
- Baseline: 150 extractions, 0 empty (0%) — JSON regex fix already resolved root cause
- Post-fix: retry safety net in place, no measurable change (already 0%)

## Status: FIXED (safety net)
