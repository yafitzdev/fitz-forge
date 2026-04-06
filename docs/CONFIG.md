# Configuration Reference

fitz-forge uses a single YAML config file. It's auto-created on first run with sensible defaults.

## Config File Location

| Platform | Path |
|----------|------|
| Windows | `%LOCALAPPDATA%\fitz-forge\fitz-forge\config.yaml` |
| macOS | `~/Library/Application Support/fitz-forge/config.yaml` |
| Linux | `~/.config/fitz-forge/config.yaml` |

The SQLite database (`jobs.db`) lives in the same directory.

Find your exact path:

```bash
python -c "from fitz_forge.config import get_config_path; print(get_config_path())"
```

---

## Root Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `"ollama"` \| `"lm_studio"` \| `"llama_cpp"` | `"ollama"` | Which LLM backend to use |

---

## Provider: Ollama

Simplest setup — install Ollama, pull a model, go.

```yaml
ollama:
  base_url: http://localhost:11434
  model: qwen2.5-coder-next:80b-instruct
  fallback_model: qwen2.5-coder-next:32b-instruct
  timeout: 300
  memory_threshold: 80.0
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | string | `http://localhost:11434` | Ollama API endpoint |
| `model` | string | `qwen2.5-coder-next:80b-instruct` | Primary model for planning |
| `fallback_model` | string \| null | `qwen2.5-coder-next:32b-instruct` | Auto-retry with this model on OOM. `null` disables fallback |
| `timeout` | int | `300` | Request timeout in seconds. Generous for initial model loading |
| `memory_threshold` | float (0-100) | `80.0` | Abort generation if system RAM usage exceeds this percentage |

---

## Provider: LM Studio

OpenAI-compatible API. Supports model switching via `lms` CLI.

```yaml
lm_studio:
  base_url: http://localhost:1234/v1
  model: qwen3-coder-30b-a3b-instruct
  smart_model: null
  fast_model: null
  fallback_model: null
  timeout: 300
  context_length: 65536
  api_key: null
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | string | `http://localhost:1234/v1` | LM Studio API endpoint |
| `model` | string | `local-model` | Default model for all operations |
| `smart_model` | string \| null | `null` | Model for reasoning tasks. `null` = use `model` |
| `fast_model` | string \| null | `null` | Model for fast/screening tasks. `null` = use `model` |
| `fallback_model` | string \| null | `null` | Fallback model. `null` = no fallback |
| `timeout` | int | `300` | Request timeout in seconds |
| `context_length` | int | `65536` | Context window size. **Split reasoning auto-enables below 32768** |
| `api_key` | string \| null | `null` | API key for OpenAI-compatible endpoints (e.g., OpenRouter) |

> **Tip:** Set `smart_model`, `fast_model`, and `model` to the same value to avoid CUDA context destruction on consumer GPUs. Model switching creates/destroys CUDA contexts, which permanently degrades performance on WDDM drivers until reboot.

---

## Provider: llama.cpp

Manages a `llama-server` subprocess directly. Best for fine-grained control.

```yaml
llama_cpp:
  server_path: /path/to/llama-server
  models_dir: /path/to/models
  port: 8012
  timeout: 300
  startup_timeout: 120
  fast_model:
    path: model.gguf
    context_size: 65536
    gpu_layers: -1
    flash_attention: true
    cache_type_k: q8_0
    cache_type_v: q8_0
```

### Server settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_path` | string | `""` | Path to `llama-server` binary |
| `models_dir` | string | `""` | Directory containing GGUF model files |
| `port` | int | `8012` | HTTP port for llama-server |
| `timeout` | int | `300` | Request timeout in seconds |
| `startup_timeout` | int | `120` | Max seconds to wait for server to become healthy |

### Model tier settings

Three tiers available: `fast_model`, `mid_model`, `smart_model`. Each has:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | string | `""` | GGUF filename (relative to `models_dir`) |
| `context_size` | int | `8192` | Context window size |
| `gpu_layers` | int | `-1` | Layers to offload to GPU. `-1` = all |
| `flash_attention` | bool | `false` | Enable flash attention (requires matching KV cache types) |
| `cache_type_k` | string \| null | `null` | KV cache quantization for keys (e.g., `q8_0`, `f16`) |
| `cache_type_v` | string \| null | `null` | KV cache quantization for values. **Must match `cache_type_k`** for flash attention |

> **Critical:** If `cache_type_k` and `cache_type_v` don't match, flash attention silently falls back to O(n^2) standard attention. Use both `q8_0` or both `f16`.

> **Tip:** Set all three tiers to the same model path. This prevents CUDA context destruction (server starts once, never restarts). See [Troubleshooting](TROUBLESHOOTING.md#wddm-gpu-performance-degradation-blackwell--consumer-cards).

---

## Agent Configuration

Controls code retrieval (powered by fitz-sage).

```yaml
agent:
  enabled: true
  max_file_bytes: 50000
  max_seed_files: 50
  source_dir: null
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable codebase context gathering. Disable for plans without code context |
| `agent_model` | string \| null | `null` | Model for agent retrieval calls. `null` = use provider's default model |
| `max_file_bytes` | int | `50000` | Maximum bytes to read per file |
| `max_seed_files` | int | `50` | Maximum files selected by retrieval. More files = richer context but larger prompts |
| `source_dir` | string \| null | `null` | Codebase directory. `null` = current working directory at runtime |

---

## Confidence Configuration

Controls the quality gate for optional API review.

```yaml
confidence:
  default_threshold: 0.7
  security_threshold: 0.9
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_threshold` | float (0-1) | `0.7` | Sections scoring below this trigger API review (if enabled) |
| `security_threshold` | float (0-1) | `0.9` | Higher bar for security-sensitive sections |

---

## Anthropic Configuration (Optional)

Enables the optional API review pass. Off by default — zero API calls unless you configure this.

```yaml
anthropic:
  api_key: null
  model: claude-sonnet-4-5-20250929
  max_review_tokens: 2048
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | string \| null | `null` | Anthropic API key. `null` = API review disabled entirely |
| `model` | string | `claude-sonnet-4-5-20250929` | Model for review calls |
| `max_review_tokens` | int (1-8192) | `2048` | Maximum tokens per review response |

Requires: `pip install "fitz-forge[api-review]"`

---

## GPU Configuration

Thermal protection for long-running plans on consumer hardware.

```yaml
gpu:
  temp_threshold: 73
  cooldown_margin: 10
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `temp_threshold` | int (0-95) | `73` | Pause LLM calls when GPU temp exceeds this (Celsius). `0` disables monitoring |
| `cooldown_margin` | int (5-30) | `10` | Resume after temp drops this many degrees below threshold |

---

## Output Configuration

```yaml
output:
  plans_dir: .fitz-forge/plans
  verbosity: normal
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `plans_dir` | string | `.fitz-forge/plans` | Where to write completed plan markdown files. Relative paths resolved against project directory |
| `verbosity` | `"quiet"` \| `"normal"` \| `"verbose"` | `"normal"` | Logging verbosity |

---

## Full Example

```yaml
provider: llama_cpp

llama_cpp:
  server_path: /usr/local/bin/llama-server
  models_dir: /models
  port: 8012
  fast_model:
    path: qwen3-coder-30b-a3b.Q6_K.gguf
    context_size: 65536
    gpu_layers: -1
    flash_attention: true
    cache_type_k: q8_0
    cache_type_v: q8_0

agent:
  enabled: true
  max_seed_files: 50

confidence:
  default_threshold: 0.7

output:
  plans_dir: .fitz-forge/plans
  verbosity: normal
```
