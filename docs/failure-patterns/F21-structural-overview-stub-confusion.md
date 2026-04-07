# F21: Compressed Source Stub Confusion

## Problem
The source code compressor replaces method bodies >6 lines with `...  # N lines`. The model interprets this as Python's Ellipsis literal (stub/abstract method convention) and concludes the method is unimplemented. This causes it to bypass existing fully-implemented layers or propose unnecessary reimplementations.

## Exact mechanism

### What the model sees (compressed source in artifact prompt)
```python
async def answer(self, query: Query, *, progress=None) -> Answer:
    """Answer a query using the full RAG pipeline."""
    ...  # 332 lines
```

### What the model concludes
Decision d3: *"their implementations (truncated as `...`) are likely consume LLM responses synchronously"*
Decision d5: *"handle_api_errors has no implementation body (only stubs)"*

### What actually exists
```python
async def answer(self, query, *, progress=None) -> Answer:
    """Answer a query using the full RAG pipeline."""
    # 332 lines: rewrite query → fast_analyze → classify → embed → retrieve
    # → read → expand → compress → assemble → guardrails → synthesize → govern
    ...
```

The model sees `...  # 332 lines` and reads it as "this method is a stub with 332 lines of comment." It then makes architectural decisions that bypass the method entirely (e.g., calling `chat_stream()` directly instead of going through the synthesizer/engine pipeline).

## Where the `...` comes from
`fitz_forge/planning/agent/compressor.py`, line 174/177:
```python
replacements[body_start] = f"{indent_str}...  # {body_lines} lines\n"
```

Bodies >6 lines are replaced. `__init__` and `_init_components` are special-cased to keep `self._xxx =` assignments. All other methods get collapsed to `...`.

## Occurrence in run 79
- **BLOCKING-GENERATE**: 2/5 plans call `generate()` (blocking) instead of `generate_stream()` because the model thinks `generate()` is a stub and doesn't understand its full implementation
- **Fabricated methods**: model invents `_build_messages_for_generation()`, `assemble_messages()`, etc. because it doesn't see what the real methods do and guesses
- **Layer bypass**: model calls `chat_stream()` directly from the engine, skipping the entire RAG pipeline (retrieval, guardrails, context assembly)

Estimated impact: ~3 pts across alignment (wrong architecture) + implementability (broken artifacts) + consistency (decisions contradict real code).

## Fix Assessment

### Option 1: Change `...` to explicit marker (RECOMMENDED)
Replace `...  # N lines` with a format that can't be confused with Python stubs:

```python
# [IMPLEMENTATION: 332 lines — body omitted for brevity]
```

or:

```python
# ... (332 lines of implementation omitted)
```

The key difference: a Python comment can't be mistaken for an Ellipsis literal. The model has no reason to think a comment means "unimplemented."

**Cost**: 0 LLM calls, 1-line change in compressor.py.
**Risk**: Low — changes what the model sees in the source section but doesn't add tokens (actually saves 3 chars: `...` → `#`).
**Confidence**: High — the root cause is unambiguous (`...` = Ellipsis literal convention), and the fix removes the ambiguity entirely.

### Option 2: Add a 1-line summary of what the method does
Instead of just `...  # N lines`, include a brief summary:

```python
# [332 lines: rewrite query, retrieve chunks, assemble context, run guardrails, synthesize answer]
```

**Cost**: Requires either AST analysis (extract key method calls from the body before compressing) or manual annotation. More complex.
**Risk**: Medium — summaries could be wrong or misleading.
**Confidence**: Medium — the summary might not be accurate enough for all methods.

### Option 3: Add explicit note in artifact prompt
Add to the rules section: *"The `...` markers in the source code mean 'body omitted for brevity', NOT 'unimplemented'. All methods with `...` have full working implementations."*

**Cost**: 0, prompt change only.
**Risk**: Low, but the note competes with 10K+ tokens of other content. The model might ignore it.
**Confidence**: Low — we already have rules like "Do NOT fabricate methods" that the model ignores when the signal from source code is stronger.

## Recommendation
Option 1 (change `...` to comment) is the clear winner. It's a 1-line change that removes the ambiguity at source. No heuristics, no prompt engineering, no LLM calls. The model will never confuse a Python comment with an unimplemented stub.

## Status: ❌ Not yet fixed
