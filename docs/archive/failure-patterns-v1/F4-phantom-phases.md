# F4: Phantom Phase References (FIXED)

## Problem
The roadmap scheduling extraction produces `critical_path` and `parallel_opportunities` that reference phase numbers not present in the `phases` array. Common patterns:
- `total_phases: 5` but only 4 phases defined
- `critical_path: [1, 2, 4]` skipping phase 3
- `parallel_opportunities: [[3, 5]]` referencing nonexistent phase 5

## Impact
- Plan has dangling references that confuse readers
- Sonnet scorer penalizes on internal consistency

## Occurrence Rate
~100% — observed in all 3 plans and in most historical plans.
**After fix:** 0% — deterministic filter removes all phantom refs.

## Root Cause
The `phases` and `scheduling` field groups are extracted in separate LLM calls. The scheduling extraction sees the reasoning text which may mention "5 phases" but the phases extraction only produced 4 concrete phases. Neither extraction knows what the other produced.

## Fix (IMPLEMENTED)
Deterministic post-extraction validation in `synthesis.py`. After both `phases` and `scheduling` are extracted:
1. Build set of valid phase numbers from `phases` array
2. Filter `critical_path` to only valid numbers
3. Filter `parallel_opportunities` to only valid number groups (drop groups with < 2 remaining)
4. Set `total_phases` = `len(phases)`
5. Filter `affected_phases` in each risk item to only valid phase numbers

Zero LLM cost. Pure code fix.

Commit: pending

## Status: FIXED
