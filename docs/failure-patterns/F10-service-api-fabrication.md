# F10: FitzService API Fabrication

## Problem
When generating SDK or route artifacts that delegate to FitzService, the model invents methods that don't exist: `service.query_stream()`, `service.retrieve()`, `service.get_provider()`, `service.get_governance_decider()`, `service.build_messages()`, `service._fast_analyze()`.

FitzService actually only has: `query()`, `point()`, `list_collections()`, `get_collection()`, `delete_collection()`, `validate_config()`, `get_config_summary()`, `health_check()`.

## Impact
- SDK and route artifacts call nonexistent methods → crash at runtime
- Sonnet scorer penalizes on codebase alignment (4-5/10) and implementability (4/10)
- This is the primary driver of floor plans (scores 32-36)

## Occurrence Rate
4/10 plans in run 63 (plans 4, 5, 6, 10). Correlates directly with the lowest scores.

## Root Cause
FitzService source is never injected into the artifact prompt. The model sees:
1. Engine.py source (via file_contents or disk) — knows engine internals
2. Route/SDK source (via file_contents) — knows the endpoint structure
3. But NOT FitzService source — doesn't know what methods it exposes

The model needs to call FitzService from routes/SDK but guesses the API.

Same root cause as F9 but for a DIFFERENT file. F9 fixed engine.py by injecting the reference method body. F10 needs FitzService's public API injected when generating artifacts that import/use it.

## Fix Options
1. **Service API injection**: When generating an artifact for a file that imports FitzService, extract FitzService's public methods and inject as context (similar to interface injection for engine attrs)
2. **Cross-file interface resolution**: Extend `_resolve_class_interfaces` to look up interfaces for types used as local variables (not just self._xxx attrs)
3. **Full source injection for small files**: FitzService is ~500 lines. If it fits in budget, inject the full source alongside the target file's source

## Affected Plans
Plans 4, 5, 10 — route and SDK artifacts that delegate to FitzService

## Test Data
- Harness: `benchmarks/test_f10_service.py`
- Baseline (no fix): 80% fabrication (40/50) — all `service.answer_stream()`
- Fix v1 (API injection only): still 80% — model sees real API but reasoning overrides
- Fix v2 (API injection + strong rule): **60%** (30/50) — rule helps but model still invents streaming methods when told to build streaming
- Remaining fabrications: `service.chat_stream()` (42%), `service.generate_stream()` (40%), `service.query_stream()` (16%)

## Root Cause (deeper)
This isn't fabrication-from-ignorance like F9. The model KNOWS FitzService doesn't have streaming methods (we inject the real API). But the reasoning instructs "build streaming endpoints that delegate to service" — the model can't reconcile "add streaming" with "service only has query()". It invents `service.chat_stream()` as the logical bridge.

The 40% of clean artifacts show the model CAN write correct bridging code (calling `service.query()` and wrapping it). But the 3B model doesn't do this consistently.

## Status: PARTIALLY FIXED (80%->60%)
Further improvement likely requires larger models or restructuring artifact generation order (generate service artifact first, then route).
