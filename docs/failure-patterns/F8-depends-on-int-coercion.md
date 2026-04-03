# F8: depends_on Integer Coercion

## Problem
LLM emits `depends_on: [1, 2]` (integers) instead of `depends_on: ["d1", "d2"]` (strings). Pydantic validation rejects the entire decomposition output, losing a valid candidate.

## Impact
- 6% of decomposition candidates fail to parse
- In best-of-2, this reduces to ~0.4% total failure (both candidates must fail), but wastes one LLM call
- Worst case: both candidates emit int deps → full stage failure

## Occurrence Rate
**Before fix:** 3/50 = 6% of decomposition outputs
**After fix:** 0/50 = 0% (Pydantic validator coerces ints to "d{n}" strings)

## Root Cause
The schema example shows `depends_on: ["d1", "d2"]` but the model sometimes outputs bare integers instead. The Pydantic `list[str]` field rejects `int` values.

## Fix (IMPLEMENTED)
Added `@field_validator("depends_on", mode="before")` to `AtomicDecision` that coerces `[1, 2]` → `["d1", "d2"]`.

Commit: pending

## Status: FIXED
