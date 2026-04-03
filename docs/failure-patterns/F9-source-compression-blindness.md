# F9: Source Compression Blindness

## Problem
When generating artifacts for large files (>8000 chars), the source code is compressed via `compress_file()` which replaces all method bodies with `... # N lines`. The model sees class structure and method signatures but **zero implementation details**.

For engine.py (58K chars -> 5.5K compressed, 10%), the model sees:
```python
def answer(self, query: Query, *, progress=None) -> Answer:
    ... # 331 lines
```

When asked to create `answer_stream()` (a streaming variant of `answer()`), the model must guess how the 331-line body chains together `_query_rewriter`, `_retrieval_router`, `_reader`, `_expander`, `_assembler`, `_governor`, `_synthesizer`. It has no reference implementation to work from.

## Impact
The model fabricates:
1. **Method signatures** — `self._query_rewriter.rewrite(query_text, None)` instead of correct args
2. **Config properties** — `self._config.krag.profile`, `self._config.hande.enabled` (nonexistent)
3. **Internal helpers** — `self._fast_analyze()`, `self._deduplicate_addresses()` (nonexistent)
4. **Orchestration logic** — wrong order, wrong data flow between components
5. **Query fields** — `query.entity_expansion_limit`, `query.history`, `query.mode` (nonexistent)

This is the **dominant remaining quality bottleneck**. All 3 Sonnet scorers flagged fabricated internal API calls as the primary issue. Codebase alignment scores: 5, 6, 5 across 3 plans.

## Occurrence Rate
100% of engine.py artifacts across 3 plans. Every plan fabricated internal implementation details.

## Root Cause
`_generate_single_artifact()` in synthesis.py:
```python
if len(source) > 8000:
    source = compress_file(source, filename)
```

Engine.py is 58K chars — well over the 8000 threshold. Compression is necessary for the prompt budget, but it removes exactly the information the model needs to create variant methods.

## What the Model DOES See (Working)
- Instance attributes via interface injection (self._xxx -> ClassName)
- Public methods on each attr (rewrite, retrieve, rerank, read, etc.)
- Return types of each method
- Class structure and method signatures

## What the Model DOESN'T See (Root Cause)
- How `answer()` actually chains the components together
- Method parameter names and types for internal calls
- Config access patterns (self._config.krag.xxx)
- Data transformations between pipeline steps
- Error handling and edge cases

## Fix Options
1. **Reference method extraction**: When the artifact purpose mentions "streaming" or "variant of method X", extract X's body from disk and inject it uncompressed as "REFERENCE IMPLEMENTATION — follow this pattern"
2. **Selective compression**: Don't compress the method most relevant to the artifact's purpose. Compress everything else.
3. **Raise compression threshold**: Increase from 8000 to 32000 chars. Would cover files up to ~32K without compression.
4. **Method body injection**: Parse the artifact decisions for method references, extract those specific method bodies from disk source, inject alongside the compressed class structure.

Option 1 or 4 is most targeted. Options 2-3 risk blowing the token budget on irrelevant code.

## Affected Stage
`synthesis.py` → `_generate_single_artifact()`, source compression at line ~1634

## Why Previous Fixes Didn't Help
- F7 (prompt reorder) fixed fabricated attr NAMES by making interface injection prominent
- F2 (field repair) fixed fabricated field NAMES via schema injection
- F5 (import repair) fixed fabricated import PATHS via structural index
- But none of these help when the model needs to understand HOW to chain components — that requires reading the actual implementation

## Test Data
- Harness: `benchmarks/test_f9_compression.py`
- Baseline (no reference): 2/50 = 4% fabrication, but artifacts were SHORT STUBS (~1700 chars avg). Model didn't attempt the real pipeline at all.
- Post-fix (reference injected): 14/50 = 28% fabrication, but artifacts are FULL IMPLEMENTATIONS (~14000 chars avg). Model now writes real code following the answer() pattern.
- False positive: `self._fast_analyze()` was flagged as fabricated but IS a real method (line 600 of engine.py). 48/50 artifacts correctly used it.
- Remaining fabrications: `query.conversation_context` (18%), `_chat_factory.get_chat()` (14%)

The fix transformed artifacts from useless stubs to real implementations with mostly-correct internal API calls. The 28% remaining fabrication is on secondary calls, not the core pipeline flow.

## Status: PARTIALLY FIXED
Reference method injection implemented. Model now produces real implementations but still fabricates some secondary calls. Next step: extend interface injection to cover factory methods and query field names.
