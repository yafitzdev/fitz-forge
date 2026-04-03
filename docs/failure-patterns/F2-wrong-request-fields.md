# F2: Wrong Request Field Names (FIXED)

## Problem
The model confuses field names across different Pydantic models. In API route artifacts, it writes `request.message` instead of `request.question`, `request.history` instead of `request.conversation_history`, and invents fields like `request.answer_mode` and `request.user_id`.

## Impact
- Generated route code would crash with AttributeError at runtime
- Sonnet scorer penalizes on codebase alignment and implementability

## Occurrence Rate
**Before F7 prompt reorder:** 40% of engine.py artifacts (20/50) had wrong fields
**After F7 prompt reorder:** 0% of engine.py artifacts (0/50)
Most common pattern was `query.conversation_context` (doesn't exist).

## Root Cause
The model sees multiple Pydantic models in the gathered context (`QueryRequest`, `ChatRequest`, `ChatResponse`, etc.) and conflates their field names. When reasoning context was placed BEFORE source code in the prompt, the model relied on memory of class names from reasoning rather than the actual source code.

## Fix (IMPLEMENTED)
Two layers:
1. **Prompt reorder (F7 fix):** Source code + schema fields placed BEFORE reasoning context. Model sees actual field names prominently. This eliminated F2 in testing.
2. **Hardcoded field repair (safety net):** `_INVALID_FIELD_PATTERNS` in `_repair_fabricated_refs()` catches remaining edge cases via regex. Codebase-specific patterns.
3. **Schema field injection:** `_resolve_schema_fields()` extracts Pydantic model field names from the structural index and injects them as "DATA MODEL FIELDS" section in the artifact prompt.

## Test Data
- Traces: `benchmarks/traces/baseline_a` (pre-reorder) vs `benchmarks/traces/fix_a_fields` (post-reorder)
- Baseline: 20/50 engine.py artifacts had `query.conversation_context` (40%)
- After: 0/50 (0%)

## Status: FIXED (by F7 prompt reorder)
