# LLM Call Quality Layer

## Problem

Every `client.generate()` call across the pipeline can produce truncated output, stray unicode, JSON extraction artifacts (trailing `"`), or unterminated strings. There are 15+ call sites, each with ad-hoc error handling. Fixes applied at one site don't propagate to others.

Specific issues found in V2 scoring (run 88):
- 6/11 parse failures = LLM stopped mid-string (truncation)
- 1/11 = JSON extraction leaked trailing `"` into content
- Hardcoded `max_tokens` (4096/8192) wastes available context budget

## Design

### 1. Context-aware max_tokens

`max_tokens` should always be `context_window - prompt_tokens`, not a hardcoded constant. The config already has `context_length=65536`. Every generate() call knows its prompt size.

```python
# Instead of:
raw = await client.generate(messages=messages, max_tokens=4096)

# Should be:
raw = await client.generate(messages=messages)
# Client internally computes: max_tokens = context_length - count_tokens(messages)
```

**CRITICAL: max_tokens must NEVER be omitted or set to the full context window.** Without a cap, llama-server enters context-shift loops: generation fills the window, server discards ~10K old tokens, model loses stop signal, repeats forever. The cap must be `remaining_budget = context_length - prompt_tokens`, which is always less than the full window.

**Formula:** `max_tokens = context_length - count_tokens(messages) - safety_margin` where `safety_margin` is ~512 tokens to prevent edge-case context shifts. If the prompt is 20K tokens and context is 65K, the model gets ~44K for output — not an artificial 4K limit that truncates 350-line engine.py artifacts.

**Token counting:** Use `tiktoken` or the provider's tokenizer. For llama-server, approximate with `len(text) // 4` (conservative). Exact counting is not needed — a 10% error in token estimation is fine when the budget is 44K vs the current 4K.

### 2. Output sanitization decorator

Wrap `client.generate()` with a post-processing layer that cleans common LLM output issues:

```python
class SanitizedLLMClient:
    """Wraps any LLM client with input/output quality checks."""
    
    def __init__(self, inner: LLMClient):
        self._inner = inner
    
    async def generate(self, **kwargs) -> str:
        # Pre-call: validate prompt fits in context
        # ...
        
        raw = await self._inner.generate(**kwargs)
        
        # Post-call: sanitize output
        raw = self._strip_trailing_json_quotes(raw)
        raw = self._fix_quadruple_docstrings(raw)
        raw = self._strip_unicode_artifacts(raw)
        
        return raw
```

### 3. Structured output validation

For calls that expect JSON, validate the structure before returning:

```python
async def generate_json(self, schema: type[BaseModel], **kwargs) -> dict:
    raw = await self.generate(**kwargs)
    parsed = extract_json(raw)
    # Validate against schema, retry once on failure
    try:
        schema(**parsed)
    except ValidationError:
        raw = await self.generate(**kwargs)  # retry
        parsed = extract_json(raw)
    return parsed
```

### 4. Truncation detection + retry

If the output looks truncated (ends mid-string, unclosed brackets), retry with higher max_tokens or a "continue" prompt:

```python
def _is_truncated(self, raw: str) -> bool:
    """Detect common truncation patterns."""
    stripped = raw.rstrip()
    # Unclosed string
    if stripped.count('"') % 2 != 0:
        return True
    # Unclosed brackets
    opens = stripped.count('{') + stripped.count('[')
    closes = stripped.count('}') + stripped.count(']')
    if opens > closes + 1:
        return True
    return False
```

### 5. Full LLM call provenance

Every `generate()` call writes a JSON trace with input messages + output string. This enables:
- Replaying any benchmark from any checkpoint
- Debugging fabrication root causes without re-running the pipeline
- Comparing prompts across runs to understand regressions

```python
async def generate(self, **kwargs) -> str:
    raw = await self._inner.generate(**kwargs)
    
    if self._trace_dir:
        self._call_count += 1
        trace = {
            "call_number": self._call_count,
            "messages": kwargs.get("messages", []),
            "output": raw,
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
            "elapsed_s": elapsed,
        }
        path = self._trace_dir / f"{self._call_count:03d}_{stage_name}.json"
        path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    
    return raw
```

Trace files go into: `results/YYYY-MM-DD_HH-MM-SS_run_NNN/plan_01/001_decomp_candidate_1.json`

## Implementation

1. Add `SanitizedLLMClient` wrapper in `fitz_forge/llm/sanitized.py`
2. Apply in `create_llm_client()` factory — all callers get it automatically
3. Remove hardcoded `max_tokens` from all 15+ call sites
4. Add `context_length` to client config (already exists)
5. Truncation retry: max 1 retry with 2x budget
6. Provenance tracing: write JSON for every generate() call when trace_dir is set

## Affected call sites

| Stage | File | Calls | Current max_tokens |
|-------|------|-------|-------------------|
| Decision decomposition | decision_decomposition.py | 2 (best-of-2) | 16384 |
| Decision resolution | decision_resolution.py | 1 per decision | 16384 |
| Synthesis reasoning | synthesis.py | 3 (best-of-3) | 16384 |
| Surgical artifact | synthesis.py | 1 per file | 8192 |
| Normal artifact | synthesis.py | 1 per file | 4096 |
| Self-critique | base.py | 1 | 16384 |
| Coherence check | synthesis.py | 1 | 16384 |
| Confidence scoring | synthesis.py | 1 | 4096 |
| Implementation check | orchestrator.py | 1 | 4096 |

The normal artifact call (4096) is the most constrained and causes the most truncation.

## Priority

High — truncation is the #1 remaining quality issue after fabrication and completeness were solved. Run 88: 11 parse failures, 6 from truncation. Fixing max_tokens alone would likely eliminate most of these.
