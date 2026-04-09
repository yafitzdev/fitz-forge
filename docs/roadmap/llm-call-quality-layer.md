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

## Implementation — DONE (run 89)

**Approach changed from wrapper to standalone function** (see discussion below).

Implemented as `fitz_forge/llm/generate.py` — a single `generate()` function that all 36 call sites use instead of `client.generate()` directly.

1. `generate()` function in `fitz_forge/llm/generate.py` — single entry point
2. All 36 call sites across 10 files migrated to use it
3. Existing `max_tokens` values preserved as intentional upper bounds (budget cap only fires when they exceed available context)
4. `configure_tracing(trace_dir)` enables per-call JSON provenance tracing
5. Truncation retry: max 1 retry with same budget

**Why standalone function instead of wrapper:** The wrapper approach (SanitizedLLMClient) requires proxying all client properties (`context_size`, `fast_model`, `drain_call_metrics()`, etc.) — fragile and breaks silently when the client interface changes. The standalone function is explicit, easy to test, and adding new pre/post processing is just adding lines to one function. The 36 call-site changes were a one-time cost; wrapper maintenance would be ongoing.

## Results (run 89, 10 plans)

| Metric | Run 88 (pre-layer) | Run 89 (post-layer) |
|--------|-------------------|---------------------|
| Avg | 84.6 | **86.8** (+2.2) |
| Range | 67-100 | 73.8-95.0 |
| Fabrications | 14 | **8** (-43%) |
| Parse failures | 11 | 16 |
| Completeness | 30/30 | 30/30 |

Truncation retry fired 6 times across 10 plans. 1 successful retry (produced longer valid output), 5 retried but still truncated (kept original). Budget capping did not fire (LM Studio context 65K, no call exceeded budget).

## Priority

Implemented. Remaining issue: truncation retries have low success rate (1/6). The retry uses the same budget — a "continue" prompt approach might work better but adds complexity.
