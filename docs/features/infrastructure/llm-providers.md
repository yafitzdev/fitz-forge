# LLM Providers

## Problem

Local LLM inference has no standard runtime. Users run Ollama, LM Studio, or
raw llama-server depending on their hardware, OS, and model preferences. Each
runtime has different APIs, model management commands, failure modes, and
performance characteristics. The planning pipeline should not care which
backend is running -- it needs to send messages and receive completions.

## Solution

Three provider implementations behind a common interface. All providers expose
`generate()`, `generate_with_tools()`, and `health_check()`. The configuration
file specifies the provider, and the worker instantiates the correct client at
startup. The pipeline stages call the client without knowing which backend is
active.

## How It Works

### Common Interface

Every provider implements:

- **`generate(messages, temperature, max_tokens, model)`** -- standard chat
  completion. Returns the response text as a string. Default `max_tokens=16384`
  on all providers prevents infinite generation from context-shift loops.

- **`generate_with_tools(messages, tools, tool_choice, model)`** -- chat
  completion with tool/function calling support. Returns the full response
  including any tool call requests.

- **`generate_with_fallback(messages)`** -- attempts generation with the
  primary model; on OOM or failure, retries with a fallback model. Returns
  `(response_text, model_used)`.

- **`health_check()`** -- verifies the backend is reachable and the configured
  model is available. Returns `True`/`False`.

- **`drain_call_metrics()`** -- returns and clears accumulated call metrics
  (timing, token counts) from `generate()` calls. Used for plan diagnostics.

### Provider 1: Ollama

`OllamaClient` in `llm/client.py` wraps the native `ollama` Python package.

- **Simplest setup**: `ollama pull model && fitz-forge plan "..."`
- **OOM fallback**: if the primary model triggers OOM (status 500 with
  `"requires more system memory"`), auto-retries with `fallback_model`.
- **Memory threshold**: aborts if system RAM exceeds threshold.
- **Retry**: `ollama_retry` (tenacity) retries on ConnectionError and transient
  HTTP statuses (408, 429, 500, 502, 503, 504), but NOT on OOM 500s.

### Provider 2: LM Studio

`LMStudioClient` in `llm/lm_studio.py` uses the OpenAI-compatible API via the
`openai` Python SDK.

- **Model switching**: `switch_model()` uses `lms load`/`lms unload` CLI
  commands. Checks `get_loaded_model()` first to avoid unnecessary restarts.
- **Tiered models**: `smart_model` and `fast_model` config fields for
  different pipeline phases.
- **Retry**: `lm_studio_retry` decorator retries on ConnectionError, httpx
  transport errors, and OpenAI SDK errors (APIConnectionError, APITimeoutError,
  and status codes 408/429/502/503/504).
- **Thinking mode**: `enable_thinking: false` is set in
  `extra_body.chat_template_kwargs` for Qwen3 models, preventing the thinking
  mode from consuming output tokens.

### Provider 3: llama.cpp

`LlamaCppClient` in `llm/llama_cpp.py` manages a `llama-server` subprocess
directly -- the most control over inference but the most operational complexity.

- **Subprocess management**: starts `llama-server` with configurable flags
  (flash attention, KV cache types, context size, GPU layers). Health checks
  poll the HTTP endpoint.
- **WDDM degradation mitigation**: on Windows consumer GPUs, each CUDA
  context create/destroy permanently degrades perf until reboot. The client
  compares model file paths before restarting -- same GGUF means no restart.
- **Tok/s baseline tracking**: warns on degradation. Only tracks outputs
  of 200+ characters (short calls have noisy measurements).
- **Flash attention + KV cache**: configurable quantization (q8_0, f16).
  Mixed KV types break flash attention -- validated at startup.
- **Retry**: `llama_cpp_retry` (5 attempts, shorter waits) handles server
  crashes, model loading latency, and transient HTTP 500s.

### Retry Logic

`llm/retry.py` defines three tenacity decorators, one per provider:

| Decorator | Attempts | Wait | Special Handling |
|-----------|----------|------|------------------|
| `ollama_retry` | 3 | 4-60s exponential | Skips OOM 500 (fallback handles it) |
| `lm_studio_retry` | 3 | 5-60s exponential | Retries httpx and OpenAI SDK errors |
| `llama_cpp_retry` | 5 | 2-30s exponential | Retries server crashes, HTTP 500 |

All decorators use `retry_if_exception()` with provider-specific predicates
that classify each exception as retryable or not.

### Infinite Generation Prevention

All providers default to `max_tokens=16384` on `generate()`. Without this cap,
llama-server enters context-shift loops: generation fills the context window,
the server discards ~10K old tokens, the model loses its stop signal, and
output repeats forever. The 16384 default accommodates the longest legitimate
stage output while preventing runaway generation. Individual calls override
this where appropriate (4096 for focused extractions and investigations).

## Key Design Decisions

1. **No abstract base class** -- the three clients share a method signature
   convention but do not inherit from a common ABC. This avoids forcing
   provider-specific features (OOM fallback, subprocess management) into a
   generic interface that would become leaky.

2. **Retry at the provider level** -- each provider has its own retry decorator
   with tuned parameters. Ollama's OOM errors need fallback, not retry.
   llama-server needs more attempts with shorter waits because server restart
   is slower. A single generic retry policy would be wrong for at least one
   provider.

3. **Same model path = no restart** -- the WDDM workaround is specific to
   Windows consumer GPUs but critical for usability. Without it, every tier
   switch destroys inference performance until reboot. Comparing file paths
   (not tier names) is the key insight -- if all tiers point to the same GGUF,
   the server never restarts.

4. **max_tokens default over per-call enforcement** -- setting the default on
   the generate method rather than requiring every caller to specify it
   prevents the failure mode where a single missing `max_tokens` causes
   infinite generation. Callers that need a different limit can override.

5. **Thinking mode disabled globally** -- `enable_thinking: false` for Qwen3
   prevents the model from entering its internal reasoning mode, which
   consumes output tokens without producing useful content for extraction.

## Configuration

Provider configuration lives in `config.yaml`:

```yaml
provider: llama_cpp  # or: ollama, lm_studio

# Ollama-specific
model: qwen3-coder-30b
fallback_model: qwen3-8b
memory_threshold: 0.9

# LM Studio-specific
smart_model: qwen3.5-35b
fast_model: qwen3.5-4b

# llama.cpp-specific
llama_server_path: /path/to/llama-server
model_path: /path/to/model.gguf
context_length: 65536
flash_attention: true
kv_cache_type: q8_0
```

## Files

| File | Role |
|------|------|
| `fitz_forge/llm/client.py` | `OllamaClient` -- Ollama native client wrapper |
| `fitz_forge/llm/lm_studio.py` | `LMStudioClient` -- LM Studio OpenAI-compatible client |
| `fitz_forge/llm/llama_cpp.py` | `LlamaCppClient` -- llama-server subprocess manager |
| `fitz_forge/llm/retry.py` | `ollama_retry`, `lm_studio_retry`, `llama_cpp_retry` decorators |
| `fitz_forge/llm/gpu_monitor.py` | GPU temperature preflight check before generate calls |

## Related Features

- [Split Reasoning](split-reasoning.md) -- auto-enabled based on
  `context_length` from provider config
- [Per-Field Extraction](per-field-extraction.md) -- uses `generate()` with
  `max_tokens=4096` for focused extractions
- [Verification Agents](verification-agents.md) -- each agent call uses
  `generate()` with `max_tokens=4096`
