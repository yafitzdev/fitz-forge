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

## Status: PARTIALLY FIXED (54% → 24% in isolated harness)

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
Three amplification layers feed fabrication into artifacts:

1. **Decisions**: Decision resolution mentions hypothetical methods ("add service.query_stream()"). These are injected UNFILTERED into the artifact prompt via `relevant_decisions`.
2. **Reasoning**: Synthesis reasoning echoes decision instructions, proposing fabricated method calls.
3. **Cross-artifact signatures**: F3 signature propagation extracts fabricated method signatures from one artifact and injects them as "match these exactly" into subsequent artifacts.

The reasoning filter (`_filter_fabricated_from_reasoning`) was applied to reasoning but NOT to decisions — leaving decisions as a wide-open backdoor. The filter also only matched `_stream` patterns, making it codebase-specific.

### Fix attempt 1: Refinement Pass (40% → 0% isolated, 60% full pipeline)
Design by user. The model writes a first-pass plan with full context (31K codebase). We then extract which files the plan actually references, trim the context to only those files (31K → 11-16K, 48-65% reduction), and re-run synthesis with the focused context.

- Result: 0/5 fabrication in isolated pipeline testing, but 60% in run 69 (10 plans)

### Fix attempt 2: Decision filter + generic fabrication detection (run 70)
Three changes:
1. Apply `_filter_fabricated_from_reasoning` to `relevant_decisions` before artifact prompt injection
2. Make the filter codebase-agnostic: match ANY `object.method(` call where method doesn't exist on any known class (no hardcoded `_stream` patterns)
3. Filter cross-artifact signatures: reject signatures containing methods not found in structural index

**Run 70 results (10 plans):**
- Plan-level: 5/10 (50%) — down from 54% (run 68)
- Artifact-level: 6/33 (18%) — significant improvement
- Almost all fabrication is in query.py (route file calling FitzService)
- engine.py, fitz.py, schemas.py mostly clean

### Fix attempt 3: Deterministic corrector (54% → 24%)

After artifact generation, AST-detect `object.method()` calls where `method` doesn't exist on the resolved type, then string-replace with the closest real method. Zero LLM cost.

Key implementation details:
- Detection uses both structural index AND disk-resolved imported type APIs (FitzService wasn't in the structural index — only 30 files selected)
- Skip list for framework objects (router, app) and common variables (token, chunk, request)
- Test classes excluded from methods lookup (prevented `engine.answer_stream → engine.test_decorator_registration`)
- Closest method found via `difflib.get_close_matches` (query_stream → query)

LLM correction prompts were tried first and ALL failed:
1. **Append correction to original prompt**: model ignores it (lost-in-the-middle, 23K chars)
2. **Prepend hard constraint**: model ignores it (decisions override)
3. **Focused correction with broken code**: model copies fabrication from shown code
4. **Stripped prompt (no decisions/reasoning)**: model still fabricates from purpose alone

The model KNOWS it's fabricating (writes comments like "this violates rules but satisfies task intent") but can't compose streaming from synchronous methods ~50% of the time. Deterministic repair is the only approach that works.

### Root cause: import graph gap (also fixed)

The import graph couldn't follow relative imports (`from .fitz_service import FitzService`), so the call graph had no edges between routes → service → engine. The decomposition couldn't trace the dependency chain and sometimes skipped the service layer. Fixed by resolving relative imports in `_extract_full_imports`.

### Key lessons
1. **Harness methodology**: Frozen-state testing misses upstream variance. The harness must vary ALL upstream stages.
2. **Attention budget**: Prompt-level instructions can't survive 11K tokens of generation. Reducing context is more effective than adding rules.
3. **Explore-then-focus**: Let the model think broadly first, then refine with focused input. Works WITH the model's natural behavior.
4. **Filter ALL inputs**: Filtering reasoning but not decisions leaves a backdoor. All text injected into artifact prompts must be filtered.
5. **Codebase-agnostic filters**: Hardcoding `_stream` patterns is fragile. Generic `object.method(` validation against the structural index works for any codebase.
6. **LLM correction can't overcome LLM fabrication**: When decisions instruct the model to build something impossible with available APIs, no prompt variation can fix it. Deterministic repair is the only reliable approach.
7. **Import graph completeness matters**: Relative imports must be resolved for the call graph to show the full dependency chain. Without it, the decomposition flies blind.
