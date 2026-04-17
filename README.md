


<div align="center">

# fitz-forge

### Architectural coding planning harness for local LLMs

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/fitz-forge.svg)](https://pypi.org/project/fitz-forge/)
[![Tests](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml/badge.svg)](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/version-0.5.0-green.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[The Problem](#the-problem) • [The Insight](#the-insight-) • [Why fitz-forge?](#why-fitz-forge) • [Benchmarks](#benchmarks) • [How It Works](#how-it-works) • [Docs](docs/features/) • [GitHub](https://github.com/yafitzdev/fitz-forge)

</div>

<br />

---

<div align="center">
<table>
  <tr>
    <td align="center" colspan="2">
      <pre><strong>Task: "Add WebSocket support to the chat API"</strong>
(Given a real codebase with FastAPI routes, Pydantic schemas, and an existing REST chat endpoint.)</pre>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <strong>❌ Raw local LLM (no harness)</strong>
<pre>
"Add a WebSocket endpoint.
 Use the websockets library.
 Create a new file for handlers.
 Add authentication middleware."
</pre>
<em>Generic advice. No file paths. No awareness
of existing code. Hallucinated library choice.
Would break the existing architecture.</em>
    </td>
    <td align="center" width="50%">
      <strong>🔨 fitz-forge (same model, same hardware)</strong>
<pre>
Phase 1: Extend ChatRouter in api/routes/chat.py
  - Add ws_chat() using existing ChatEngine
  - Reuse AuthMiddleware.verify_token()
  - Test: pytest tests/api/test_chat_ws.py

Phase 2: Adapt MessageSchema in schemas/chat.py
  - Add ws_message field (matches existing
    ChatMessage.content structure)
  - Verify: pydantic model_validate()
</pre>
<em>Real files. Real methods. Phased roadmap
with verification commands. Grounded in
the actual codebase.</em>
    </td>
  </tr>
</table>

→ Same model, same hardware. The difference is the harness: `fitz-forge` reads your codebase, reasons in stages, self-critiques, 
and extracts structured output that a small model can actually produce reliably.

</div>

---

### Where to start 🚀

> [!IMPORTANT]
> Requires [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), or [llama.cpp](https://github.com/ggerganov/llama.cpp) 
> with a loaded model. Also needs [fitz-sage](https://github.com/yafitzdev/fitz-sage) for code retrieval.

```bash
pip install fitz-forge

fitz plan "Add OAuth2 authentication with Google and GitHub providers"
```

That's it. Your plan runs overnight on local hardware.

---

### About

I built `fitz-forge` because the best AI coding tools are dangerously dependent on subsidized API pricing. 
Claude Code costs $100/month *today* — heavily subsidized. When those subsidies shrink, the planning phase alone 
(understanding a codebase, reasoning about architecture, producing a structured plan) could cost more than the subscription. 
`fitz-forge` moves that expensive planning phase onto hardware you already own. No API costs. No data leaving your network. 
And as local models improve, your plans improve for free.

No LangChain. No LlamaIndex. Every layer written from scratch, with code retrieval powered by [fitz-sage](https://github.com/yafitzdev/fitz-sage).

~20k lines of Python. 970+ tests. Built by Yan Fitzner ([LinkedIn](https://www.linkedin.com/in/yan-fitzner/), [GitHub](https://github.com/yafitzdev)).

![fitz-forge llm_with_harness](https://raw.githubusercontent.com/yafitzdev/fitz-forge/main/docs/assets/llm_with_harness.jpg)

---

### Why `fitz-forge`?

**Cut your Opus bill — plan locally, implement with Sonnet 💸**
> Agentic planning is the most expensive part of the process, and it's where LLMs struggle the most. `fitz-forge` 
> produces a markdown artifact you hand to Sonnet for implementation. The expensive tokens never hit your API budget.

**Dumb local models produce smart plans 🧠**
> The pipeline breaks the task into atomic decisions, resolves each against relevant files, then narrates the committed 
> decisions into a plan. Suddenly a local model can produce plans that would overwhelm it in a single prompt.

**Runs on whatever hardware you've got 🖥️**
> Consumer GPU? Models like `Qwen3.6-35-a3b` or `Gemma4-26B-A4b` do the whole pipeline. CPU-only box or tiny VRAM? Run a medium model at 10 tok/s 
> overnight. Tokens-per-second stops mattering when you're sleeping.

**Drops into Claude Code or Codex via CLI and MCP 🔌**
> Expose `fitz-forge` as an MCP server (`fitz serve`) and it becomes a tool inside Claude Code, or any MCP-capable client.
> Same principle as with CLI. Tell Claude to create a plan using `fitz-forge`, and it does the heavy lifting locally while you wait.

**Any codebase, any language 🌐**
> Python, Go, Rust — the retrieval layer indexes by file structure and imports, and the grounding layer validates generated 
> artifacts against whatever the codebase actually contains.

**Queue a job. Go to sleep. Relax. Let it run overnight. 🌙**
> Every stage produces checkpoints. Power outage at minute 15 of a 20-minute run? `fitz retry <id>` picks up from the 
> last completed stage.

**Fully local execution possible 🏠**
> Ollama, LM Studio, or llama.cpp. No API keys required to start.

---

### Benchmarks

TBD

---

### How It Works

A 10-stage pipeline that decomposes architectural planning into small, focused LLM calls interleaved with deterministic 
AST work. Retrieval + implementation check feed a decision-based reasoning core (decompose → resolve → synthesize), then 
artifacts are generated, closure-checked, and grounded against the real codebase before the plan is written.

<br>

```
     USER PROMPT
          │
          ▼
┌─────────────────────────────────────────┐
│ 1. Agent Context Gathering    [6-8 LLM] │  retrieval + compression
├─────────────────────────────────────────┤
│ 2. Implementation Check       [1 LLM]   │  already built?
├─────────────────────────────────────────┤
│ 3. Call Graph Extraction      [0 · AST] │  deterministic
├─────────────────────────────────────────┤
│ 4. Decision Decomposition     [2-4 LLM] │  adaptive best-of-N
├─────────────────────────────────────────┤
│ 5. Decision Resolution        [10-15]   │  1 call per decision
├─────────────────────────────────────────┤
│ 6. Synthesis                  [~15 LLM] │  reasoning + 13 extractions
├─────────────────────────────────────────┤
│ 7. Artifact Generation        [3-8 LLM] │  per-artifact + closure checks
├─────────────────────────────────────────┤
│ 8. Grounding Validation       [0-5 LLM] │  AST + repair
├─────────────────────────────────────────┤
│ 9. Coherence Check            [1 LLM]   │  cross-stage sanity
├─────────────────────────────────────────┤
│ 10. Render + Write            [0]       │  markdown to disk
└─────────────────────────────────────────┘
          │
          ▼
    ~/.fitz-forge/plans/plan_<id>.md

Total: ~40-60 LLM calls · ~7-9 min on RTX 5090
```

| # | Stage | Docs |
|---|-------|------|
| 1 | Agent Context Gathering | [01_agent-context-gathering.md](docs/features/pipeline/01_agent-context-gathering.md) |
| 2 | Implementation Check | [02_implementation-check.md](docs/features/pipeline/02_implementation-check.md) |
| 3 | Call Graph Extraction | [03_call-graph-extraction.md](docs/features/pipeline/03_call-graph-extraction.md) |
| 4 | Decision Decomposition | [04_decision-decomposition.md](docs/features/pipeline/04_decision-decomposition.md) |
| 5 | Decision Resolution | [05_decision-resolution.md](docs/features/pipeline/05_decision-resolution.md) |
| 6 | Synthesis | [06_synthesis.md](docs/features/pipeline/06_synthesis.md) |
| 7 | Artifact Generation | [07_artifact-generation.md](docs/features/pipeline/07_artifact-generation.md) |
| 8 | Grounding Validation | [08_grounding-validation.md](docs/features/pipeline/08_grounding-validation.md) |
| 9 | Coherence Check | [09_coherence-check.md](docs/features/pipeline/09_coherence-check.md) |
| 10 | Render + Write | — |

<br>

> [!NOTE]
> The pipeline decomposes a problem that would overwhelm a small model into many small LLM calls it can handle reliably. 
> Each per-field JSON extraction is under 2000 chars — small enough for a 3B quantized model to produce valid output. 
> Deterministic AST work (call graph, grounding check) carries the structural load so LLMs only do what LLMs are good at.

Full pipeline docs: **[docs/features/](docs/features/)** — detailed docs covering every stage and infrastructure component.

---

<details>

<summary><strong>📦 Quick Start</strong></summary>

<br>

```bash
# Install
pip install fitz-forge

# Queue a job
fitz plan "Build a plugin system for data transformations"

# Start the background worker
fitz run

# Check on it
fitz status 1

# Read the plan
fitz get 1
```

**Optional extras:**
```bash
pip install "fitz-forge[api-review]"    # Anthropic API review pass
pip install "fitz-forge[lm-studio]"    # LM Studio provider (openai SDK)
pip install "fitz-forge[dev]"          # pytest, build tools
```

**Prerequisites:**
- Python 3.10+
- [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), or [llama.cpp](https://github.com/ggerganov/llama.cpp) with a loaded model
- [fitz-sage](https://github.com/yafitzdev/fitz-sage) for code retrieval

</details>

---

<details>

<summary><strong>📦 CLI Reference</strong></summary>

<br>

```bash
fitz plan "description"   # Queue a planning job
fitz run                  # Start background worker (Ctrl+C to stop)
fitz list                 # Show all jobs
fitz status <id>          # Check progress
fitz get <id>             # Print completed plan as markdown
fitz retry <id>           # Re-queue failed/interrupted job
fitz confirm <id>         # Approve optional API review
fitz cancel <id>          # Skip API review, finalize plan
fitz serve                # Start MCP server
```

**Job lifecycle:**
```
QUEUED -> RUNNING -> COMPLETE
                  -> AWAITING_REVIEW -> QUEUED (confirm) / COMPLETE (cancel)
                  -> FAILED / INTERRUPTED (both retryable)
```

</details>

---

<details>

<summary><strong>📦 MCP Server</strong></summary>

<br>

Plug into Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "fitz-forge": {
      "command": "fitz",
      "args": ["serve"]
    }
  }
}
```

**MCP Tools:**

| Tool | Description |
|------|-------------|
| `create_plan` | Queue a new planning job |
| `check_status` | Check job progress |
| `get_plan` | Retrieve completed plan |
| `list_plans` | List all planning jobs |
| `retry_job` | Retry a failed job |
| `confirm_review` | Approve API review after seeing cost |
| `cancel_review` | Skip API review, finalize plan |

</details>

---

<details>

<summary><strong>📦 Configuration</strong></summary>

<br>

Auto-created on first run:

| Platform | Path |
|----------|------|
| Windows | `%LOCALAPPDATA%\fitz-forge\fitz-forge\config.yaml` |
| macOS | `~/Library/Application Support/fitz-forge/config.yaml` |
| Linux | `~/.config/fitz-forge/config.yaml` |

Database (`jobs.db`) lives in the same directory.

```yaml
# LLM provider: "ollama", "lm_studio", or "llama_cpp"
provider: lm_studio

lm_studio:
  base_url: http://localhost:1234/v1
  model: qwen3-coder-30b-a3b-instruct    # single model for retrieval + reasoning
  smart_model: null                        # null = use model for all tiers
  fast_model: null                         # null = use model for all tiers
  timeout: 600
  context_length: 65536                    # split reasoning auto-enables below 32768

ollama:
  base_url: http://localhost:11434
  model: qwen2.5-coder-next:80b-instruct
  fallback_model: qwen2.5-coder-next:32b-instruct  # OOM fallback (null to disable)
  timeout: 300
  memory_threshold: 80.0  # RAM % threshold to abort

llama_cpp:
  server_path: /path/to/llama-server
  models_dir: /path/to/models
  port: 8012
  fast_model:
    path: model.gguf
    context_size: 65536
    gpu_layers: -1
    flash_attention: true
    cache_type_k: q8_0
    cache_type_v: q8_0

agent:
  enabled: true
  max_file_bytes: 50000
  max_seed_files: 50    # files available via inspect_files/read_file tools
  source_dir: null      # null = cwd at runtime

confidence:
  default_threshold: 0.7
  security_threshold: 0.9

anthropic:
  api_key: null  # null = API review disabled
  model: claude-sonnet-4-5-20250929

output:
  plans_dir: .fitz-forge/plans
  verbosity: normal
```

</details>

---

<details>

<summary><strong>📦 Architecture</strong> → <a href="docs/ARCHITECTURE.md">Full Architecture Guide</a></summary>

<br>

```
CLI (typer)   --> tools/ --> SQLiteJobStore <-- BackgroundWorker --> PlanningPipeline
MCP (fastmcp) --> tools/ --> SQLiteJobStore
```

```
fitz_forge/
├── cli.py                     # Typer CLI (9 commands)
├── server.py                  # FastMCP server + lifecycle
├── __main__.py                # python -m fitz_forge (MCP stdio)
├── tools/                     # Service layer
├── models/                    # JobStore ABC, SQLiteJobStore, JobRecord
├── background/                # BackgroundWorker, signal handling
├── llm/                       # LLM clients (Ollama, LM Studio, llama.cpp), retry
├── planning/
│   ├── pipeline/stages/       # 3 stages (split or combined) + orchestrator + checkpoints
│   ├── agent/                 # Code retrieval bridge to fitz-sage
│   ├── prompts/               # Externalized .txt prompt templates
│   └── confidence/            # Per-section confidence scoring
├── api_review/                # Anthropic review client + cost calculator
├── config/                    # Pydantic schema + YAML loader
└── validation/                # Input sanitization
```

</details>

---

<details>

<summary><strong>📦 Development</strong></summary>

<br>

```bash
git clone https://github.com/yafitzdev/fitz-forge.git
cd fitz-forge
pip install -e ".[dev]"  # editable install for development
pytest  # 970+ tests

# Lint
ruff check fitz_forge/
ruff format --check fitz_forge/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide and [examples/](examples/) for usage examples.

**Benchmark factory** for A/B testing pipeline changes:
```bash
# Retrieval benchmarks (~12s/run)
python -m benchmarks.plan_factory retrieval --runs 10 --source-dir ../your-project

# Reasoning benchmarks with fixed retrieval
python -m benchmarks.plan_factory reasoning --runs 5 --source-dir ../your-project \
  --context-file benchmarks/ideal_context.json --split --max-seeds 5
```

</details>

---

### License

MIT

---

### Links

- [GitHub](https://github.com/yafitzdev/fitz-forge)
- [PyPI](https://pypi.org/project/fitz-forge/)
- [Changelog](CHANGELOG.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Feature Docs](docs/features/) — 17 detailed docs covering every pipeline stage and infrastructure component
- [Configuration Reference](docs/CONFIG.md) — every config field explained
- [Troubleshooting](docs/TROUBLESHOOTING.md) — GPU issues, Windows quirks, pipeline debugging
- [Contributing](CONTRIBUTING.md)
- [Examples](examples/)
