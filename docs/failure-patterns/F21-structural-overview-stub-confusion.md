# F21: Parallel Variant Pipeline Shortcutting

## Problem (Reframed)
When the model creates a parallel variant of an existing method (streaming, async, batch, cached), it sometimes **shortcuts to a low-level primitive** instead of replicating the original method's internal pipeline. The variant looks functional but silently drops all intermediate processing (validation, retrieval, enrichment, guardrails, etc).

This is NOT caused by the `...  # N lines` compression format (tested: all alternatives were worse). It's caused by the model seeing a convenient low-level API and taking the shortcut.

## Generic Example

**Original method** (complex pipeline):
```python
def process(self, input):
    validated = self._validator.validate(input)      # step 1
    enriched = self._enricher.enrich(validated)      # step 2  
    transformed = self._transformer.transform(enriched)  # step 3
    result = self._generator.generate(transformed)   # step 4 (final)
    self._auditor.log(result)                        # step 5
    return result
```

**Correct variant** (replicate pipeline, change one step):
```python
def process_stream(self, input):
    validated = self._validator.validate(input)      # step 1 - SAME
    enriched = self._enricher.enrich(validated)      # step 2 - SAME
    transformed = self._transformer.transform(enriched)  # step 3 - SAME
    for token in self._generator.generate_stream(transformed):  # step 4 - CHANGED
        yield token
    self._auditor.log(...)                           # step 5 - SAME
```

**What the model produces** (shortcut):
```python
def process_stream(self, input):
    # Skips steps 1-3 entirely
    for token in self._generator.generate_stream(input):  # goes straight to step 4
        yield token
```

## Concrete Example (fitz-sage streaming task)

**Original**: `FitzKragEngine.answer()` — 332-line RAG pipeline:
```
query → rewrite → analyze → classify → embed → retrieve → read → expand 
→ compress → assemble → guardrails → synthesize → govern → Answer
```

**Model's shortcut**: `answer_stream()` calls `self._chat.chat_stream()` directly — sends raw query to LLM with no retrieval, no context, no guardrails.

## Why the Model Shortcuts

The model receives:
1. **Compressed source** with `...  # 332 lines` — can't see pipeline steps in source section
2. **Full reference method** (16K chars) — CAN see the pipeline in the REFERENCE section
3. **Available methods** listing `self._chat` has `chat_stream()` — a tempting shortcut

The model follows the shortcut when the upstream reasoning/decisions already describe it. The artifact prompt has the right reference but the model's attention is split between 16K of reference code and 15K of reasoning that may describe a simpler architecture.

## Harness Measurements

| Format | F21 shortcut rate | Notes |
|--------|------------------|-------|
| `...  # N lines` (baseline) | 15% (3/20) | Model sometimes ignores reference |
| `pass  # N lines` | 95% (19/20) | Model reads `pass` as empty — much worse |
| `# [implemented] N lines` | 100% (20/20) | Model ignores comments — worst |

Format changes DON'T fix this. The model shortcuts because it has a simpler design in mind, not because it thinks methods are stubs.

## Proposed Fix: Tool-Based Surgical Rewrite

Instead of giving the model 16K of reference + 15K of reasoning and asking "write a streaming variant," decompose into two focused calls:

**Call 1 (identify delta)**: Give fresh context with JUST the reference method. Ask: "Which line/call is the final generation step that should change for a streaming variant?"

**Call 2 (apply delta)**: Give fresh context with the reference method + the identified delta. Ask: "Copy this method exactly, but replace line X with Y. Change nothing else."

Benefits:
- Each call has **one instruction** — can't shortcut when told "copy exactly, change one line"
- **Fresh context** — no competing 15K of reasoning suggesting a simpler architecture
- **Codebase agnostic** — works for any "parallel variant" task in any codebase
- The delta identification can be deterministic for common patterns (streaming, async)

## Status: ❌ Not yet fixed — tool-based surgical rewrite proposed, not implemented
