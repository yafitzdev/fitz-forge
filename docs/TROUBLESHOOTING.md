# Troubleshooting

Common issues and solutions for fitz-forge, especially on consumer GPU hardware.

---

## GPU & VRAM Issues

### WDDM GPU Performance Degradation (Blackwell / consumer cards)

**Symptom:** After running a few plans, inference speed drops permanently. Restarting the app doesn't help. Only a reboot fixes it.

**Cause:** On Windows consumer GPUs (WDDM driver), each CUDA context creation and destruction permanently degrades performance until reboot. There's no persistence mode on consumer cards (unlike data center GPUs with TCC driver).

**Fix:** fitz-forge avoids this by design — all model tiers use the same GGUF file, so the llama-server starts once and never restarts. If you're using different models per tier, set them all to the same path:

```yaml
llama_cpp:
  fast_model:
    path: same-model.gguf    # all three point to the same file
  mid_model:
    path: same-model.gguf
  smart_model:
    path: same-model.gguf
```

If degradation has already occurred, reset your GPU: press `Ctrl+Win+Shift+B` (Windows GPU driver reset) or reboot.

---

### Mixed KV Cache Types Break Flash Attention

**Symptom:** Catastrophic slowdown — prefill drops to 0.2%/sec at 14K tokens. Model appears to hang.

**Cause:** Setting mismatched KV cache types (e.g., `cache_type_k: f16` and `cache_type_v: q8_0`) disables flash attention silently. CUDA kernels lack compiled paths for mismatched types and fall back to O(n^2) standard attention.

**Fix:** Use matching types for both K and V:

```yaml
llama_cpp:
  fast_model:
    cache_type_k: q8_0    # must match
    cache_type_v: q8_0    # must match
    flash_attention: true
```

Both `q8_0` works well for VRAM savings. Both `f16` works but uses more VRAM. Don't mix them.

---

### f16 KV Cache at 65K Context Spills to System RAM

**Symptom:** Generation drops to ~10 tok/s on an RTX 5090 (should be 500+ tok/s).

**Cause:** f16 KV cache at 65536 context exceeds 32GB VRAM, spilling to system RAM. PCIe bandwidth becomes the bottleneck.

**Fix:** Use `q8_0` KV cache instead:

```yaml
llama_cpp:
  fast_model:
    cache_type_k: q8_0
    cache_type_v: q8_0
    context_size: 65536
```

---

## Infinite Generation

### llama-server Context-Shift Loops

**Symptom:** Generation runs forever, never producing a stop token. Output repeats or loops.

**Cause:** Without a `max_tokens` cap, generation fills the context window. llama-server discards ~10K old tokens (context shift), the model loses the stop signal, and repeats indefinitely.

**Fix:** fitz-forge sets `max_tokens=16384` by default on all `generate()` calls. Per-call overrides use `4096` for investigations/extractions. If you're seeing runaway generation, ensure your config has sufficient `context_size` (>= 32768, ideally 65536).

---

### Qwen3 Thinking Mode Consuming Tokens

**Symptom:** Output contains `<think>...</think>` blocks that consume context budget, leaving less room for actual plan content.

**Fix:** fitz-forge sets `enable_thinking: false` in `extra_body.chat_template_kwargs` for both llama_cpp and lm_studio clients. This is handled automatically — no config needed.

---

## Windows-Specific Issues

### WMI Deadlock with pytest (MS Store Python 3.12)

**Symptom:** `pytest` hangs on startup. No tests execute.

**Cause:** On Windows with MS Store Python 3.12, `faker` (via `setuptools` entry points) triggers a WMI query that deadlocks with `platform._wmi_query`. This happens before any test code runs.

**Fix:** fitz-forge patches this automatically. The `pyproject.toml` includes `-p fitz_forge` in `addopts`, which forces early import of `fitz_forge/__init__.py` — that module patches `platform._wmi_query` before setuptools entry points load.

If you're still hitting this, ensure your venv has fitz-forge installed in dev mode:

```bash
pip install -e ".[dev]"
```

---

## Configuration Issues

### Two Config Files on Windows (MS Store Python)

**Symptom:** Config changes don't take effect even after editing the file.

**Cause:** MS Store Python uses a virtualized filesystem. Two config paths exist:
- `%LOCALAPPDATA%\fitz-forge\fitz-forge\config.yaml` (native Python)
- `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.12_...\LocalCache\Local\fitz-forge\...` (MS Store Python)

**Fix:** Check which Python you're running (`python -c "import sys; print(sys.executable)"`) and edit the config file in the matching location. Or switch to a non-MS-Store Python install.

---

### Config Not Loading

**Symptom:** fitz-forge uses default values even though you edited config.yaml.

**Fix:** Run `fitz plan "test"` once to auto-create the config, then check the path:

```bash
python -c "from fitz_forge.config import get_config_path; print(get_config_path())"
```

Edit the file at that exact path.

---

## Pipeline Issues

### Plan Quality Is Poor

**Symptom:** Plans have generic advice, hallucinated file paths, or miss existing code.

**Possible causes:**

1. **Agent disabled:** Check `agent.enabled: true` in config. Without the agent, the pipeline has no codebase context.

2. **Wrong source_dir:** The agent reads from `source_dir` (config) or the current working directory. If you're running from the wrong directory, the agent finds nothing.

3. **Model too small:** Models under 7B parameters struggle with the reasoning passes. The pipeline is designed for 27B+ models. Qwen3-Coder-30B (MoE, 3B active) is the recommended minimum.

4. **Context too small:** If `context_length < 32768`, split reasoning auto-enables, which is good. But below 8192, even split mode can't fit enough context for quality output.

---

### Pipeline Takes Too Long

**Typical timings on consumer hardware (RTX 5090, Qwen3-Coder-30B):**
- Agent gathering: 20-40s
- Context stage: 2-5 min
- Architecture + Design: 10-20 min
- Roadmap + Risk: 5-10 min
- Post-processing: 1-2 min
- **Total: 20-40 min**

If your pipeline takes significantly longer:

1. **Check tok/s:** The llama-server logs prompt processing speed. If it drops below 100 tok/s, check for GPU degradation (see above).
2. **Reduce context:** Lower `max_seed_files` from 50 to 30 to reduce prompt size.
3. **Skip verification agents:** They add 6 LLM calls. Currently not configurable — but you can check if they're the bottleneck from the stage timing logs.

---

### Checkpoint Corruption

**Symptom:** `fitz retry <id>` fails with a JSON decode error.

**Fix:** The checkpoint is stored in `jobs.pipeline_state` column in SQLite. If corrupted:

```bash
# Clear the checkpoint (pipeline restarts from scratch)
python -c "
import sqlite3, json
from fitz_forge.config import load_config
cfg = load_config()
conn = sqlite3.connect(cfg.db_path)
conn.execute('UPDATE jobs SET pipeline_state = NULL WHERE id = ?', ('JOB_ID',))
conn.commit()
"
```

Replace `JOB_ID` with your job ID.

---

## Getting Help

- [GitHub Issues](https://github.com/yafitzdev/fitz-forge/issues)
- Check [docs/features/](docs/features/) for detailed pipeline documentation
- Check [docs/ARCHITECTURE.md](ARCHITECTURE.md) for system overview
