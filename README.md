


<div align="center">

# fitz-forge

### Overnight AI architectural planning on local hardware. Queue a job. Go to sleep. Wake up to a plan.

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

→ Same 30B model, same hardware. The difference is the harness: fitz-forge reads your codebase, reasons in stages, self-critiques, and extracts structured output that a small model can actually produce reliably.

</div>

---

### Where to start 🚀

> [!IMPORTANT]
> Requires [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), or [llama.cpp](https://github.com/ggerganov/llama.cpp) with a loaded model. Also needs [fitz-sage](https://github.com/yafitzdev/fitz-sage) for code retrieval.

```bash
pip install fitz-forge

fitz plan "Add OAuth2 authentication with Google and GitHub providers"
fitz run        # start background worker
fitz status 1   # check progress
fitz get 1      # read the finished plan
```

That's it. Your plan runs overnight on local hardware.

---

### About

I built fitz-forge because the best AI coding tools are dangerously dependent on subsidized API pricing. Claude Code costs $100/month *today* — heavily subsidized. When those subsidies shrink, the planning phase alone (understanding a codebase, reasoning about architecture, producing a structured plan) could cost more than the subscription. fitz-forge moves that expensive planning phase onto hardware you already own. No API costs. No data leaving your network. And as local models improve, your plans improve for free.

No LangChain. No LlamaIndex. Every layer written from scratch, with code retrieval powered by [fitz-sage](https://github.com/yafitzdev/fitz-sage).

~20k lines of Python. 970+ tests. Built by Yan Fitzner ([LinkedIn](https://www.linkedin.com/in/yan-fitzner/), [GitHub](https://github.com/yafitzdev)).

---

### The Problem

The single most expensive operation in agentic LLM coding is the **planning phase**: understanding a codebase, reasoning about architecture, producing a structured plan. Every token burns through your API budget. And raw local LLMs can't do this well — ask a 30B model to plan a feature and you get generic advice with hallucinated file paths, no awareness of your existing code, and no structured output.

What if local models could produce *good* plans — grounded in your codebase, structured into phases, with real file paths and verification commands?

---

### The Insight 💡

Running LLMs locally means balancing three things: **tokens per second**, **quantization quality**, and **model intelligence**. A 70B model at high quant gives you excellent reasoning but crawls at 2-5 tok/s on consumer hardware. That feels unusable — until you realize planning doesn't need to be interactive.

> **Queue a job. Go to sleep. Let it run overnight.**
>
> Suddenly tok/s doesn't matter. You can run a large, intelligent model purely in RAM at 10 tok/s and that's *fine*.

```
10 tok/s × 60s × 60min × 8 hours = 288,000 tokens
```

That's enough for a full architectural plan — reasoning, self-critique, structured extraction — from a model running on hardware you already own.

---

### Why fitz-forge?

**Reads your codebase first 🔍** → [Agent Context Gathering](docs/features/pipeline/00_agent-context-gathering.md)
> An agent builds a structural index of your codebase (classes, functions, imports), selects relevant files via LLM scan, expands through import chains and `__init__.py` facades, and auto-includes architectural hub files. Reasoning stages see a compact file manifest (~4K tokens) with on-demand `inspect_files` and `read_file` tools — 50+ files fit in 32K context.

**Per-field extraction that small models can handle 🧩** → [Per-Field Extraction](docs/features/infrastructure/per-field-extraction.md)
> Each stage does 1 reasoning pass + 1 self-critique + N tiny JSON extractions (<2000 chars each). Even a 3B model can reliably produce structured output at this scale. Failed extractions get Pydantic defaults instead of crashing the stage — partial plan > no plan.

**Single model, zero swapping 🔀** → [LLM Providers](docs/features/infrastructure/llm-providers.md)
> Qwen3-Coder-30B (MoE, 3B active) handles both retrieval and reasoning — benchmarked at 89% critical recall across 40 queries, faster than the 4B it replaced. No model switching, no VRAM churn. Split reasoning mode breaks large LLM calls into ~8K-token pieces, enabling dense 27B models at 32K context.

**Crash recovery built in 🔄** → [Crash Recovery](docs/features/infrastructure/crash-recovery.md)
> Jobs checkpoint to SQLite. Machine crashes mid-plan? `retry` picks up from the last checkpoint. Power goes out overnight? Resume in the morning.

**5 verification agents catch mistakes 🔬** → [Verification Agents](docs/features/infrastructure/verification-agents.md)
> After the main reasoning pass, 5 agents run in parallel: contract extraction, data flow tracing, pattern matching, type boundary auditing, and assumption surfacing. They catch hallucinated method calls and architectural gaps before the plan finalizes.

**Claude where it counts, local everywhere else 🎯** → [Confidence Scoring](docs/features/pipeline/06_confidence-scoring.md)
> The local model does the heavy lifting — 95% of the tokens. Per-section confidence scoring flags weak spots, and those sections can pause for an Anthropic API review pass. Fully optional — off by default, zero API calls unless you opt in.

**Two interfaces, same engine 🔌**
> CLI for background job queues, MCP server for Claude Code / Claude Desktop integration. Both wrap the same `tools/` service layer and SQLite job store.

**More features at a glance:**
> - [x] **Three LLM providers.** [Ollama](docs/features/infrastructure/llm-providers.md) (with OOM fallback), LM Studio (OpenAI-compatible), or llama.cpp (managed subprocess with flash attention).
> - [x] **[Split reasoning.](docs/features/infrastructure/split-reasoning.md)** Architecture and design as separate calls, roadmap and risk as separate calls. Reduces peak context from ~29K to ~8K tokens per call.
> - [x] **[Cross-stage coherence check.](docs/features/pipeline/05_coherence-check.md)** Post-pipeline pass verifies context → architecture → roadmap consistency.
> - [x] **[Implementation detection.](docs/features/pipeline/01_implementation-check.md)** Surgical check prevents planning to build what already exists.
> - [x] **[Grounding validation.](docs/features/infrastructure/grounding-validation.md)** AST-based verification that generated artifacts reference real methods, not hallucinated ones.

---

### Benchmarks

Can a structured pipeline make a local 40B model plan like a 140B one? 20 plans each, scored by a 6-dimension Sonnet-as-Judge rubric (/60).

```
  Qwen3-Coder-REAP-40B-A3B (raw)         ██████░░░░░░░░░░░░░░░░░░░░░░░░░  18.4 /60
  + fitz-forge                           ██████████████████████░░░░░░░░░  45.1 /60   (+145%)
  + sonnet judge                         █████████████████████████░░░░░░  51.2 /60   (+178%)

  Qwen3-Coder-30B-A3B (raw)              ████░░░░░░░░░░░░░░░░░░░░░░░░░░░  12.3 /60
  + fitz-forge                           ████████████████████░░░░░░░░░░░  40.3 /60   (+228%)

  ───────────────────────────────────────────────────────────────────────────────────────────
  Claude Sonnet 4.6                      █████████████████████████░░░░░░  51.8 /60
  Claude Opus 4.6                        ████████████████████████████░░░  56.9 /60
```

> A 40B local model with fitz-forge scores **45.1** — closing 78% of the gap to Sonnet 4.6.
> Add the optional Sonnet judge pass and it matches frontier at **51.2**.

**Scoring:** 6-dimension Sonnet-as-Judge rubric — file identification, contract preservation, internal consistency, codebase alignment, implementability, scope calibration. Each dimension scored 1-10, total /60. 20 plans per configuration.

> [!NOTE]
> These are preliminary numbers from early eval runs. Final validated benchmarks are in progress.

---

### How It Works

A retrieval agent pre-stage followed by 3 planning stages. [Split reasoning](docs/features/infrastructure/split-reasoning.md) mode breaks architecture+design and roadmap+risk into separate LLM calls for smaller context models. Each stage uses [per-field extraction](docs/features/infrastructure/per-field-extraction.md): one reasoning prompt produces analysis, a self-critique pass catches scope inflation and hallucinated files, then small JSON extractions pull structured data from the reasoning.

<br>

```
  [Agent]    structural index → LLM scan → import expand → facade expand → hub auto-include
                 |
                 v
  [Check]    implementation check — is this task already built?
                 |
                 v
  [Stage 1]  Context — requirements, constraints, assumptions (4 field groups)
  [Stage 2]  Architecture + Design — split or combined (6 field groups)
               Split mode: architecture reasoning → design reasoning (each ~8K tokens)
  [Stage 3]  Roadmap + Risk — split or combined (3 field groups)
               Split mode: roadmap reasoning → risk reasoning (each ~8K tokens)
                 |
                 v
  [Post]     coherence check → confidence scoring → optional API review → render markdown
```

<br>

> [!NOTE]
> The pipeline decomposes a problem that would overwhelm a small model into pieces it can handle reliably. Each JSON extraction is <2000 chars — small enough for a 3B quantized model to produce valid output. Split reasoning auto-enables when `context_length < 32768`, letting dense 27B models run the full pipeline.

Full pipeline docs: **[docs/features/](docs/features/)** — 13 detailed feature docs covering every stage and infrastructure component.

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
