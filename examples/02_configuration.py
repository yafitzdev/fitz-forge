# examples/02_configuration.py
"""
Configuration: How to set up different LLM providers.

fitz-forge auto-creates a config file on first run. This example shows
what each provider section looks like and how to tune it.

Config location:
    Windows:  %LOCALAPPDATA%\\fitz-forge\\fitz-forge\\config.yaml
    macOS:    ~/Library/Application Support/fitz-forge/config.yaml
    Linux:    ~/.config/fitz-forge/config.yaml
"""

# ============================================================
# Provider: Ollama (simplest setup)
# ============================================================
OLLAMA_CONFIG = """
provider: ollama

ollama:
  base_url: http://localhost:11434
  model: qwen2.5-coder:32b-instruct
  fallback_model: qwen2.5-coder:14b-instruct  # OOM fallback (null to disable)
  timeout: 300
  memory_threshold: 80.0  # RAM % threshold to abort
"""

# ============================================================
# Provider: LM Studio (OpenAI-compatible API)
# ============================================================
LM_STUDIO_CONFIG = """
provider: lm_studio

lm_studio:
  base_url: http://localhost:1234/v1
  model: qwen3-coder-30b-a3b-instruct
  smart_model: null   # null = use model for all tiers
  fast_model: null    # null = use model for all tiers
  timeout: 600
  context_length: 65536  # split reasoning auto-enables below 32768
"""

# ============================================================
# Provider: llama.cpp (managed subprocess)
# ============================================================
LLAMA_CPP_CONFIG = """
provider: llama_cpp

llama_cpp:
  server_path: /path/to/llama-server
  models_dir: /path/to/models
  port: 8012
  fast_model:
    path: model.gguf
    context_size: 65536
    gpu_layers: -1         # -1 = all layers on GPU
    flash_attention: true
    cache_type_k: q8_0     # quantized KV cache saves VRAM
    cache_type_v: q8_0     # must match cache_type_k for flash attention
"""

# ============================================================
# Agent configuration (code retrieval)
# ============================================================
AGENT_CONFIG = """
agent:
  enabled: true
  max_file_bytes: 50000
  max_seed_files: 50    # files available in planning context
  source_dir: null      # null = current working directory at runtime
"""

# ============================================================
# Optional: Anthropic API review
# ============================================================
API_REVIEW_CONFIG = """
# pip install "fitz-forge[api-review]"
anthropic:
  api_key: sk-ant-...   # null = API review disabled
  model: claude-sonnet-4-5-20250929

confidence:
  default_threshold: 0.7   # sections below this trigger API review
  security_threshold: 0.9  # higher bar for security-sensitive sections
"""

if __name__ == "__main__":
    print("This file is a configuration reference.")
    print("Copy the relevant sections to your config.yaml.")
    print()

    from fitz_forge.config import get_config_path

    print(f"Your config file: {get_config_path()}")
