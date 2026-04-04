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

## Status: PARTIALLY FIXED (isolated 0%, full pipeline 54%)

### Isolated fix (artifact generation only): 48%→0%
Four-layer fix:
1. Imported type API injection (shows real methods)
2. Explicit rules + prompt reorder (rules+grounding FIRST, reasoning last)
3. Lost-in-the-middle fix (FitzService API was buried in the middle)
4. **Compose-from-existing rule**: "If the method you need does NOT exist on a dependency, compose the behavior from its existing methods instead of inventing new ones"

Layer 4 was the breakthrough: 48%→0% in 50 isolated runs.

### Full pipeline (run 68, 48 plans): 54% fabrication
The isolated harness was **flawed**: it froze one decomposition+resolution+reasoning and generated 50 artifacts from that same state. If that one reasoning didn't mention `service.answer_stream()`, all 50 were clean.

In the full pipeline, each plan gets a fresh reasoning. When the **synthesis reasoning** writes "delegate to `service.query_stream()`" as the design, the artifact generator faithfully implements it — no artifact-level rule can override upstream reasoning.

### Run 68 fabrication breakdown (48 plans)
- `service.query_stream(`: 13 plans (27%)
- `service.chat_stream(`: 9 plans (19%)
- `service.answer_stream(`: 7 plans (15%)
- `request.query` (should be `.message`): 5 plans (10%)
- Clean plans: 22/48 (46%)

### Root Cause (updated)
The fabrication originates during long-form synthesis reasoning generation. With ~50K prompt and ~11K output, attention drifts and the model loses grounding on which methods actually exist. Prompt-level fixes (reorder, cheatsheet, evidence removal) all failed or made it worse.

### Fix: Refinement Pass (40% → 0%)
Design by user. The model writes a first-pass plan with full context (31K codebase). We then extract which files the plan actually references, trim the context to only those files (31K → 11-16K, 48-65% reduction), and re-run synthesis with the focused context. The model's attention is now concentrated on the files that matter.

- First pass: exploratory, full context, may fabricate
- Refinement pass: focused, trimmed context, grounded
- Cost: 1 extra synthesis reasoning call (~20-30s)
- Result: 0/5 fabrication in pipeline testing (baseline 40%)

### Key lessons
1. **Harness methodology**: Frozen-state testing misses upstream variance. The harness must vary ALL upstream stages.
2. **Attention budget**: Prompt-level instructions can't survive 11K tokens of generation. Reducing context is more effective than adding rules.
3. **Explore-then-focus**: Let the model think broadly first, then refine with focused input. Works WITH the model's natural behavior.
