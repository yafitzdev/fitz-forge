# F2: Wrong Request Field Names (PARTIALLY FIXED)

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

## Recurrence (run 73, 2026-04-06)

The F7 fix eliminated F2 in engine.py artifacts but **not in route artifacts**. 3/5 plans in run 73 had schema cross-contamination in route code:
- `request.question` instead of `request.message` (ChatRequest context)
- `request.conversation_history` instead of `request.history`
- `request.messages` instead of `request.history`
- `request.source` (doesn't exist on ChatRequest)

The model still conflates `QueryRequest` and `ChatRequest` fields when generating streaming route endpoints. The schema field injection only helps when the model pays attention to it — in long generation contexts (route artifacts with SSE boilerplate), it drifts back to wrong fields.

### Fix: F25 per-function artifact decomposition (2026-04-06)

Root cause was not schema injection — the model had the right data. The problem was `_extract_reference_method` picking `query()` (longest body) as the reference for ALL new endpoints, including `/chat/stream`. The model was told "follow this pattern exactly" with the wrong handler.

**Fix**: `_decompose_multi_handler_artifacts` splits file-level artifacts into per-function artifacts when the source file has multiple route handlers. Each artifact gets a focused purpose like "streaming variant of chat()" and `_extract_reference_method` correctly picks `chat()` as the reference.

**Result**: wrong_field violations 83% → 0% (run 74 → run 77).

## Status: ✅ FIXED (engine.py by F7 prompt reorder, route artifacts by F25 per-function decomposition)
