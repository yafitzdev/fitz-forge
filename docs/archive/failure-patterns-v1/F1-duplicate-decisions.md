# F1: Duplicate Decisions (FIXED)

## Problem
The decomposition stage sometimes produces duplicate decisions. In plan 1 (run 61), decisions d8-d15 were verbatim copies of d4 — 8 identical decisions asking the same question about `chat_stream` in `StreamingChatProvider`.

## Impact
- Wastes resolution budget (8 unnecessary LLM calls resolving the same question)
- Inflates decision count, misleading downstream stages
- Sonnet scorer penalizes heavily on internal consistency

## Occurrence Rate
**Before fix:** 8/47 = 17% of decomposition outputs had duplicates (39 total duplicate pairs)
**After fix:** 0% — deterministic dedup in `execute()` removes all duplicates

## Root Cause
The decomposition LLM generates at temperature=0.3 (for best-of-2 variance). At non-zero temperature, the model sometimes enters a repetition loop where it re-emits the same decision structure with minor variations or exact copies.

## Fix (IMPLEMENTED)
Post-parse dedup in `execute()`: after parsing decisions, compare each pair using `SequenceMatcher` on the `question` field. If similarity >= 0.85, drop the later duplicate. Also prunes dangling `depends_on` references to removed decisions.

Commit: pending

## Affected Stage
`decision_decomposition.py` → `execute()`, after `parse_output()`, before coverage gate

## Test Data
- Harness: `benchmarks/test_f1_dedup.py`
- Baseline: 50 runs, 8/47 had duplicates (3 failed to parse — see F8)
- Post-fix: 50 runs, 0/50 parse failures, dedup catches remaining 10% raw duplicates

## Status: FIXED
