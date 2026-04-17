# LLM Providers

## Problem

Local LLM inference has no standard runtime. Users run Ollama, LM Studio, or
raw llama-server depending on their hardware, OS, and model preferences. Each
runtime has its own model management story, failure modes, and performance
characteristics. The planning pipeline should not care which backend is
running -- it needs to send messages and receive completions.

## Solution

All three backends speak an OpenAI-compatible `/v1/chat/completions` endpoint,
so a single `OpenAIApiClient` base class implements streaming generation,
tool calling, monitoring, and metric tracking once. Each provider is a thin
subclass adding only its own lifecycle (CLI or subprocess management) and
context-window preflight.

## How It Works

### Shared Base: `OpenAIApiClient`

`llm/openai_api.py` implements:

- **`generate(messages, temperature, max_tokens=16384, model=None)`** --
  streaming chat completion via `AsyncOpenAI`. Applies `_strip_thinking()`
  post-processing, records per-call metrics, and triggers the optional
  `GPUTemperatureGuard.preflight()` / `maybe_throttle()` hooks.
- **`generate_with_tools(messages, tools, tool_choice="auto")`** -- one-shot
  tool/function call. Accepts Python callables and builds OpenAI tool schemas
  from their signatures via `_callable_to_openai_tool`.
- **`generate_with_fallback(messages)`** -- calls `generate()` and returns
  `(text, model)`. No OOM fallback -- providers either handle their own
  memory or do not.
- **`generate_with_monitoring(messages, monitor)`** -- runs `MemoryMonitor`
  in parallel and raises `MemoryError` if the RAM threshold trips.
- **`health_check()`** -- GET `/v1/models`; returns True on 200. Subclasses
  override to add context-window preflight or CLI-based auto-load.
- **`ensure_model(model_name, context_size=None)`** -- default no-op.
  Subclasses override to drive CLI/subprocess lifecycle.
- **`tool_result_message(tool_call_id, content)`** -- standard OpenAI
  `role=tool` dict.
- **`drain_call_metrics()`** -- returns and clears accumulated call metrics.

All callers see this interface, irrespective of provider.

### Thinking Mode Suppression

`OpenAIApiClient.__init__` takes `disable_thinking: bool = True` (default).
When true, every chat request is sent with
`extra_body={"chat_template_kwargs": {"enable_thinking": False}}` — this
suppresses the Qwen3 family's internal reasoning mode, which would otherwise
burn output tokens without producing useful content. Set `disable_thinking=
False` for providers that do not tolerate the Qwen chat-template extension
(vanilla vLLM, for example).

### Provider 1: Ollama (`llm/ollama.py`)

Trivial subclass. Ollama exposes an OpenAI-compatible endpoint at
`<base>/v1/chat/completions`. `OllamaClient` normalises the configured
`base_url` so the `/v1` suffix is always present, inherits the default
`health_check` (GET `/v1/models`), and leaves `ensure_model` as the base
no-op — Ollama pulls on demand.

There is no `ollama` Python SDK dependency. There is no OOM fallback path
(if the model does not fit, the user switches models externally).

### Provider 2: LM Studio (`llm/lm_studio.py`)

Inherits the base and implements `ensure_model`, `switch_model`, `unload_model`,
`reload_model`, `is_model_loaded`, and `get_loaded_model` on top of the `lms`
CLI. The overridden `health_check` adds a minimum context-window preflight
(`_MIN_CONTEXT_TOKENS = 8_192`) and auto-loads the configured model when
nothing is loaded. `switch_model` stays on the interface for manual reloads
but is not called from any planning code path.

### Provider 3: llama.cpp (`llm/llama_cpp.py`)

Inherits the base and wraps a `llama-server` subprocess. The client owns
`start` / `stop` / `ensure_model` / `_ensure_alive` plus the WDDM-aware
`TokSecBaseline` (tracks prefill tok/s across runs, triggers
`_auto_reset_gpu` via Ctrl+Win+Shift+B when degradation is detected).

`generate()` is an override — not because of the chat protocol, but because
the prefill-vs-generate timing split feeds `TokSecBaseline`. The base's
`_strip_thinking` and `_extra_body` helpers are reused verbatim. The server
hosts a single model for the whole session; no tier switching means no CUDA
context destruction on WDDM consumer GPUs.

### Retry Logic

`llm/retry.py` defines one decorator, `openai_api_retry`, applied in
`OpenAIApiClient.generate`. Five attempts with exponential backoff
(2-30s). `is_openai_api_retryable` classifies as retryable:

- `ConnectionError`
- `httpx.ConnectError`, `httpx.ReadTimeout`, `httpx.ConnectTimeout`
- `openai.APIConnectionError`, `openai.APITimeoutError`
- `openai.APIStatusError` with status in `{408, 429, 500, 502, 503, 504}`
- `RuntimeError` whose message mentions `llama-server`, `crashed`, or
  `exited` (covers `LlamaCppClient._ensure_alive` restart failures).

One decorator is used for all three providers because, once the transport
is OpenAI-compatible, the transient-failure taxonomy is the same.

### Infinite Generation Prevention

`generate()` defaults to `max_tokens=16384`. Without this cap, llama-server
enters context-shift loops: generation fills the context window, the server
discards ~10K old tokens, the model loses its stop signal, and output
repeats forever. The 16384 default accommodates the longest legitimate
stage output; individual calls override this where appropriate (4096 for
focused extractions and investigations).

## Key Design Decisions

1. **Single base class over duck-typed siblings** -- previously each client
   re-implemented ~300 LOC of identical streaming/tool/monitoring code.
   Unifying on `OpenAIApiClient` makes each subclass responsible only for
   its own lifecycle, and guarantees that a bug fix in streaming applies
   everywhere.

2. **One retry predicate** -- the three legacy per-provider retry decorators
   converged on the same exception types once Ollama moved off its SDK.
   Keeping them separate would have been pure duplication.

3. **No OOM fallback path** -- Ollama's old OOM-to-fallback-model dance was
   the only reason OOM 500s were special-cased. Users control which model
   loads at the runtime level (`ollama run`, `lms load`, llama-server
   arguments); a second in-process fallback added no value.

4. **Single model per session (llama.cpp)** -- the WDDM workaround is
   specific to Windows consumer GPUs but critical for usability. Serving
   one model for the whole session means the server starts once and CUDA
   contexts are never destroyed — preserving inference performance.

5. **Thinking mode disabled globally** -- Qwen3's thinking mode consumes
   output tokens without producing extractable content. `disable_thinking`
   is a constructor arg so providers that cannot parse the extension can
   opt out.

## Configuration

Provider configuration lives in `config.yaml`:

```yaml
provider: llama_cpp  # or: ollama, lm_studio

# Ollama-specific
ollama:
  base_url: http://localhost:11434
  model: qwen3-coder-30b
  fallback_model: qwen3-8b   # kept for interface parity; no in-process OOM path

# LM Studio-specific
lm_studio:
  model: qwen3.5-35b

# llama.cpp-specific
llama_cpp:
  server_path: /path/to/llama-server
  models_dir: /path/to/models
  model:
    path: model.gguf
    context_size: 65536
    gpu_layers: -1
```

## Files

| File | Role |
|------|------|
| `fitz_forge/llm/openai_api.py` | `OpenAIApiClient` base: streaming, tool calling, monitoring, metrics |
| `fitz_forge/llm/ollama.py` | `OllamaClient` -- passthrough to Ollama's `/v1` endpoint |
| `fitz_forge/llm/lm_studio.py` | `LMStudioClient` -- `lms` CLI lifecycle |
| `fitz_forge/llm/llama_cpp.py` | `LlamaCppClient` -- llama-server subprocess + tok/s baseline + GPU reset |
| `fitz_forge/llm/retry.py` | `openai_api_retry` decorator + `is_openai_api_retryable` |
| `fitz_forge/llm/gpu_monitor.py` | GPU temperature preflight check before generate calls |

## Related Features

- [Per-Field Extraction](per-field-extraction.md) -- uses `generate()` with
  `max_tokens=4096` for focused extractions
- [Crash Recovery](crash-recovery.md) -- the worker reuses a single client
  across resumed jobs
