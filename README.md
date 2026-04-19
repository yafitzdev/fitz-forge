


<div align="center">

# fitz-forge

### Architectural coding planning harness for local LLMs

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/fitz-forge.svg)](https://pypi.org/project/fitz-forge/)
[![Tests](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml/badge.svg)](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/version-0.6.2-green.svg)](CHANGELOG.md)
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

→ Same model, same hardware. The difference is the harness: `fitz-forge` reads your codebase, reasons in stages, runs a 
senior-engineer review at every stage output, and extracts structured output that a small model can actually produce reliably.

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

Two premises behind `fitz-forge`:

**1. Agentic coding tools are heavily subsidized today.** A $100/month Claude Code subscription gets you enough frontier-model inference that the raw API cost, if metered, would run multiples of the price. This works while AI providers are still buying mindshare. It doesn't work forever.

**2. Planning is the most expensive phase of agentic coding.** More tokens than implementation. More than debugging. More than code review. A single "plan this refactor" request reads the whole codebase, reasons about architecture, and emits a structured plan — the phase with the highest context requirement and the fuzziest stopping condition.

Put those two together: when subsidies normalize, the cost of agentic coding will rise, and the biggest single line item will be planning. `fitz-forge` moves that phase onto hardware you already own. A local 26B model produces the plan overnight; you hand it to Claude Code (or an agent, or a human) for implementation. The subsidized-but-metered tokens go to the cheaper-per-token phases. The planning tokens never leave your machine.

No LangChain. No LlamaIndex. Every layer written from scratch, with code retrieval powered by [fitz-sage](https://github.com/yafitzdev/fitz-sage).

~20k lines of Python. 1200+ tests. Built by Yan Fitzner ([LinkedIn](https://www.linkedin.com/in/yan-fitzner/), [GitHub](https://github.com/yafitzdev)).

![fitz-forge llm_with_harness](https://raw.githubusercontent.com/yafitzdev/fitz-forge/main/docs/assets/llm_with_harness.jpg)

---

### Measured cost per plan

Concrete numbers for the "planning is the most expensive phase" premise. Same task (`streaming_implementation` on the `fitz-sage` repo), same user prompt, two modes:

| Mode | What it does | Tokens | Cost/plan | Time |
|---|---|---|---:|---:|
| 🤖 Pure Claude Code (Sonnet 4.6, plan-mode) | Reads the codebase, reasons, produces a plan | ~12K output + ~194K cached context | **$0.96** | 6.5 min |
| 🔨 fitz-forge (gemma-4-26b on RTX 5090) | Same job, local pipeline | 0 API tokens | **~$0.02** electricity | 12 min |

**Per-plan savings: ~$0.94.** Trivial on a single plan, compounds on real usage.

At conservative workloads — one plan a workday, ~30 plans/month — that's **$28/month, or $336/year**, on planning alone. Power users hitting several plans a day see it scale linearly: 100 plans/month is ~$93/month of Claude Code planning spend → ~$1,100/year. All of it runs overnight on hardware you already own.

<br>

> [!NOTE]
> N=1 data point, Sonnet 4.6 pricing as of 2026-04-19 ($3/MTok input, $15/MTok output, $0.30/MTok cache reads). Local electricity cost assumes 575W draw for 12 min at US residential rates. Pricing, token usage, and your own usage pattern will shift the numbers — run `python -m benchmarks.claude_code_baseline` with your own task to calibrate. The point isn't the exact dollar figure; it's the order of magnitude.

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

<details>

<summary><strong>📦 How we verify the pipeline works</strong></summary>

<br>

> [!IMPORTANT]
> **Plans you generate with `fitz-forge` do not come with a score.** The evaluation framework below is how we (the developers) measure the pipeline's quality — for regression testing, for comparing changes, and for driving improvements. It never runs in production. Your plan is just a plan.

Ideally we'd want every local-model output to come with an "is this plan any good?" number. Running a Sonnet-grader on every plan would defeat the purpose of local-first — you're back to paying API tokens on the critical path. So instead we judge the *pipeline* rigorously offline, and ship a pipeline we've measured.

Three evaluation mechanisms, each operating at a different timescale:

**Benchmarks** (~60 min per run). The full A/B: run the pipeline N times on a challenge task, score each plan. Lives in `benchmarks/challenges/<task>/`. Each challenge has a fixed user prompt, a curated file list (so retrieval isn't a variable), and a hand-authored taxonomy that defines what "good" means for that task in 4-6 quality tiers. The `plan_factory.py` runner produces plans; the V2 scorer (see [SCORER-V2-SPEC.md](docs/SCORER-V2-SPEC.md)) evaluates them on five dimensions:

- **Coverage** — did the required files ship with real implementations (not `raise NotImplementedError` stubs)?
- **Craft** — is the code quality good on what was produced?
- **Groundedness** — do the plan's references resolve in the real codebase?
- **Actionability** — can an agent execute the plan end-to-end (are there concrete verification commands)?
- **Architectural correctness** — is the plan's overall approach right? Graded by Sonnet against the task's taxonomy, which means this is the one dimension that depends on a frontier model — but it runs *only at evaluation time*, not during the pipeline.

The first four are deterministic. Same plan in, same score out, forever. The fifth adds Sonnet's judgment for the architectural-level question, which a deterministic checker can't assess.

**Replay** (~5-10 min). When we change something in a single stage — a review's prompt, a regeneration mechanism, the artifact coverage check — we don't want to re-run decomposition and resolution (which are expensive and irrelevant to the change). `benchmarks/plan_factory.py replay` loads a checkpoint snapshot from a previous run and continues from there. Changed the design review? Replay from `snapshot_after_decision_resolution.json` to validate the fix in 5 minutes instead of 12. Most of our iteration happens at this speed.

**The Fixer Loop** ([benchmarks/FIXER_LOOP.md](benchmarks/FIXER_LOOP.md)). The methodology that ties it all together. When a task is scoring below where we want it: enumerate the failure patterns from Tier-2's qualitative classifications, log each into a task-specific bug register with an impact score, fix the highest-impact one first, re-benchmark, confirm the fix lands on both tiers. Every fix has to be codebase- and language-agnostic — task-specific hacks don't ship. The track record section of FIXER_LOOP.md lists every benchmark improvement cycle and what changed.

Results below are the output of this process.

</details>

---

### Benchmarks

**Three arms, same task, same five-dimension evaluation.** All three arms get the same user prompt and the same 30 relevant source files (curated in `ideal_context.json`) embedded in their prompt. Tools are disabled so no arm can read additional files agentically — this is a controlled experiment. The only variables are *which model* and *whether the fitz-forge harness runs*.

- 🤖 **Raw gemma** — one shot, local model, no harness.
- 🔨 **fitz-forge** — same local model, wrapped in the harness.
- 🧠 **Cold Claude Code** (Sonnet 4.6) — one shot, frontier model, no harness.

<br>

#### Streaming implementation on `fitz-sage` · n=5 per arm

| Metric | 🤖 Raw gemma (no harness) | 🔨 gemma + fitz-forge | 🧠 Cold Claude Code (Sonnet) |
|---|---:|---:|---:|
| **Coverage** (required files delivered, not stubbed) | 50.0 | **100.0** | 90.0 |
| **Craft** (code quality on evaluated files, 0 if missing/stubbed) | 34.0 | **100.0** | 57.0 |
| **Groundedness** (refs resolve in real codebase; missing/stubbed = ungrounded) | 26.6 | **100.0** | 40.0 |
| **Actionability** (phases with real verification commands) | 0.0 | **100.0** | 0.0 |
| **Architectural correctness** | 38.8 | **89.5** | 83.0 |
| Cost per plan (API tokens) | $0 | $0 | **$0.40** |
| Cost per plan (electricity) | ~$0.00 | ~$0.02 | — |
| Latency per plan | 28s | 12 min | 108s (wall clock parallel: 2.4 min) |
| Tokens per plan | — | — | ~65K cache write + ~11K output |

> Craft and Groundedness are scored over the taxonomy's *evaluated* file set (engine, routes, synthesizer, schemas, SDK). A file that's missing from the plan, or shipped as a `raise NotImplementedError` stub, scores 0 for that file — an empty promise isn't "well-crafted code." The fabrication detector's lookup is augmented with definitions from every artifact in the plan, so a class defined in a sibling file (e.g. `StreamEvent` in `schemas.py`) counts as real when it's referenced from elsewhere.

<br>

The story:

**Raw gemma is the worst on every dimension.** Half the required files missing or stubbed; the rest of the scores follow — stubs can't carry Craft, missing files can't be grounded, no roadmap means no Actionability, and the Sonnet grader rates the overall architecture as bottom-tier.

**Cold Claude Code beats raw gemma on every dimension** — as you'd expect from a frontier model. More files shipped, better code on them, higher architectural tier. Real capability gap.

**But Cold Claude Code still loses to fitz-forge on every deterministic dimension.** Not because Sonnet is a worse coder — because the harness enforces things a one-shot call can't.

- **Coverage** (90 vs 100): 1/5 Sonnet plans still drops a required file. The harness's coverage review catches this and regenerates the missing artifact.
- **Craft** (57 vs 100): one-shot Sonnet has real fabrications — methods it references but never defines (`self._prepare_query`, `self._post_generate`). It also doesn't cover every evaluated file in every plan. fitz-forge's grounding + repair loop + coverage review catch both classes of defect before the plan finalizes.
- **Groundedness** (40 vs 100): same shape. Real fabrications plus third-party imports the scorer can't verify. fitz-forge's closure check expands missing symbols into sibling artifacts or repairs the reference, landing at 0 violations on every run.
- **Actionability** (0 vs 100): neither one-shot arm emits a roadmap with verification commands. fitz-forge's synthesis stage produces structured phases every time because that's what its schema demands.

**Architectural correctness is the one dimension where Claude Code and fitz-forge are close** (83 vs 89.5 — within variance). Both produce plans that implement the full streaming pipeline; fitz-forge lands on A1 (the ideal pattern) 4/5 times, Claude Code lands on it 1/5.

**The headline:** fitz-forge adds *structure + rigor + end-to-end coverage* — the things a closed-loop agent needs — at 20× less cost per plan than Claude Code. That's the value prop: **rigor on a budget, not competing-with-Sonnet on raw capability**.

<br>

> [!NOTE]
> Variance on a single run is significant (Tier-2 range for raw gemma was 6.25–62.5, harness 72.5–100, Claude Code 75.0–87.5). We report 5-plan means. One run is a data point, not a headline.
>
> Sonnet pricing as of 2026-04-19 ($3/MTok input, $15/MTok output, $0.30/MTok cache reads). Local electricity cost assumes 575W draw for 12 min at US residential rates.

<br>

Reproduction:
```bash
# Arm 1: raw gemma (no harness)
python -m benchmarks.no_harness \
  --source-dir ../fitz-sage \
  --context-file benchmarks/challenges/streaming_implementation/ideal_context.json \
  --query "$(cat benchmarks/challenges/streaming_implementation/user_prompt.txt)" \
  --taxonomy benchmarks/challenges/streaming_implementation/taxonomy.json \
  --runs 5 --score-v2

# Arm 2: gemma + fitz-forge harness
python -m benchmarks.plan_factory decomposed \
  --runs 5 --source-dir ../fitz-sage \
  --context-file benchmarks/challenges/streaming_implementation/ideal_context.json \
  --query "$(cat benchmarks/challenges/streaming_implementation/user_prompt.txt)" \
  --taxonomy benchmarks/challenges/streaming_implementation/taxonomy.json \
  --score-v2

# Arm 3: cold Claude Code (Sonnet, parallel one-shots, tools disabled)
python -m benchmarks.claude_code_benchmark \
  --source-dir ../fitz-sage \
  --context-file benchmarks/challenges/streaming_implementation/ideal_context.json \
  --query "$(cat benchmarks/challenges/streaming_implementation/user_prompt.txt)" \
  --taxonomy benchmarks/challenges/streaming_implementation/taxonomy.json \
  --runs 5 --score-v2
```

---

### How It Works

A 10-stage pipeline that decomposes architectural planning into small, focused LLM calls interleaved with deterministic 
AST work. Retrieval + implementation check feed a decision-based reasoning core (decompose → resolve → synthesize), then 
artifacts are generated, closure-checked, and grounded against the real codebase before the plan is written. **A senior-
engineer review layer wraps the whole pipeline** — six narrow critique passes, each scoped to one stage's output, that 
regenerate the affected stage when a review flags issues.

<br>

```
     USER PROMPT
          │
          ▼
┌─── 🧑‍💼 SENIOR ENGINEER REVIEW LAYER ──────────────────────────────┐
│   detect → regenerate → re-review → keep whichever is better      │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │                                                         │      │
│  │   1. Agent Context Gathering       [6-8 LLM]            │      │
│  │   2. Implementation Check          [1 LLM]              │      │
│  │   3. Call Graph Extraction         [0 · AST]            │      │
│  │   4. Decision Decomposition        [2-4 LLM]   ◂───────────────── decomposition review
│  │   5. Decision Resolution           [10-15]              │      │
│  │   6. Synthesis                     [~18 LLM]            │      │
│  │      ├─ context assumptions                    ◂───────────────── assumption review
│  │      ├─ architecture pick                      ◂───────────────── architecture review
│  │      └─ design spec                            ◂───────────────── design review
│  │   7. Artifact Generation           [3-8 LLM]            │      │
│  │      ├─ per-file code                          ◂───────────────── semantic review
│  │      └─ set-level coverage                     ◂───────────────── coverage review
│  │   8. Grounding Validation          [0-5 LLM]            │      │
│  │   9. Coherence Check               [1 LLM]              │      │
│  │  10. Render + Write                [0]                  │      │
│  │                                                         │      │
│  └─────────────────────────────────────────────────────────┘      │
│                                                                   │
│  Fail-safe: any review error keeps the original output unchanged. │
└───────────────────────────────────────────────────────────────────┘
          │
          ▼
    ~/.fitz-forge/plans/plan_<id>.md

Total: ~45-70 LLM calls · ~8-12 min on RTX 5090
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
| ★ | **Senior Engineer Reviews** (wraps every stage) | **[senior-engineer-reviews.md](docs/features/infrastructure/senior-engineer-reviews.md)** |

<br>

> [!NOTE]
> The pipeline decomposes a problem that would overwhelm a small model into many small LLM calls it can handle reliably. 
> Each per-field JSON extraction is under 2000 chars — small enough for a 3B quantized model to produce valid output. 
> Deterministic AST work (call graph, grounding check) carries the structural load so LLMs only do what LLMs are good at.

> [!TIP]
> **The senior-engineer review layer is the quality multiplier.** A local model writing a plan is a junior engineer left 
> unsupervised — it picks plausible-sounding wrong patterns, under-specifies interfaces, and builds on assumptions the 
> codebase contradicts. Each review is one narrow LLM critique ("what would a senior say about this stage's output?") 
> that hands its feedback back to the stage for regeneration. Reviews are strictly additive: they can only improve the 
> plan or leave it unchanged. See **[Senior Engineer Reviews](docs/features/infrastructure/senior-engineer-reviews.md)** 
> for the full design.

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
  model:
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
