<!-- README.md -->

<div align="center">

# fitz-forge

### Agentic coding-planning harness for local LLMs.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/fitz-forge.svg)](https://pypi.org/project/fitz-forge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.3-green.svg)](CHANGELOG.md)
[![Tests](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml/badge.svg)](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[Planning Artifact](#planning-artifact) • [Why `fitz-forge`?](#why-fitz-forge) • [Pipeline](#pipeline) • [Evaluation](#evaluation) • [Documentation](#links) • [GitHub](https://github.com/yafitzdev/fitz-forge)

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
      <strong>❌ Raw local LLM</strong>
<pre>
"Add a WebSocket endpoint.
 Use the websockets library.
 Create handlers.
 Add auth middleware."
</pre>
    </td>
    <td align="center" width="50%">
      <strong>🔨 fitz-forge</strong>
<pre>
Phase 1: Extend api/routes/chat.py
  - Reuse existing ChatService
  - Validate token with current auth layer
  - Test: pytest tests/api/test_chat_ws.py

Phase 2: Adapt schemas/chat.py
  - Preserve current ChatMessage shape
  - Verify: pydantic model_validate()
</pre>
    </td>
  </tr>
</table>

  → `fitz-forge` turns a broad coding request into grounded context, reviewed decisions, artifact code, and a verification roadmap.
</div>

---

### Where to start 🚀

> [!IMPORTANT]
> `fitz-forge plan` runs local-first by default. A local model does the planning work, fitz-sage handles code retrieval,
> SQLite stores jobs and checkpoints, and optional API review only runs when you configure it.

```bash
pip install fitz-forge

fitz-forge prep
fitz-forge plan "Add WebSocket support to the chat API"
```

The result is a planning artifact: repository context, implementation status, architectural decisions, proposed code
artifacts, review findings, and commands a human or coding agent can use to verify the work.

---

### About

`fitz-forge` is a working LLM harness for agentic coding planning. It reads a real repository, gathers relevant context,
decomposes the requested change, generates implementation artifacts, validates cross-file consistency, and writes a plan
that a human or coding agent can execute.

⭐ The planning pipeline is deliberately decomposed. Instead of one giant prompt, local models work through focused
retrieval, implementation checks, decision decomposition, decision resolution, synthesis, artifact generation, and review
passes.

⭐ The harness carries the exactness. fitz-sage retrieval, structural indexes, AST passes, call graphs, grounding checks,
and artifact-set closure checks catch classes of mistakes that prompt-only planning tends to miss.

⭐ Evaluation is built around golden-plan taxonomies. For benchmark tasks, a strong model such as Claude Sonnet helps
author the ideal plan taxonomy, and generated plans are scored against that explicit answer key.

Yan Fitzner — ([LinkedIn](https://www.linkedin.com/in/yan-fitzner/), [GitHub](https://github.com/yafitzdev), [HuggingFace](https://huggingface.co/yafitzdev)).

![fitz-forge LLM harness](docs/assets/llm_with_harness.jpg)

---

### Why `fitz-forge`?

**Planning artifact as the contract 🧾**
> Every job produces a markdown artifact with codebase context, implementation status, decisions, proposed artifacts,
> review findings, risks, and verification commands. The plan is meant to be inspected, handed to a coding agent, or used
> by a human engineer.

**Harnessed local models 🧠**
> Local coding models are much more useful when the harness narrows each call to something the model can do reliably:
> inspect this context, resolve this decision, write this artifact, critique this stage.

**Codebase-grounded context 🗂️** → [Agent Context Gathering](docs/features/pipeline/01_agent-context-gathering.md)
> fitz-sage retrieval, structural indexes, interface signatures, import context, and AST-derived summaries feed the
> planning pipeline before design decisions are made.

**Senior-engineer review layer 🧑‍💼** → [Senior Engineer Reviews](docs/features/infrastructure/senior-engineer-reviews.md)
> Narrow review passes critique decomposition, assumptions, architecture, design, semantic artifact quality, and artifact
> coverage. A failed review can regenerate the affected stage; a failed review pass leaves the original output unchanged.

**Closed artifact sets 🔒**
> Artifact generation is checked as a set, not just one file at a time. Closure checks look for missing cross-file symbols,
> unresolved imports, invalid kwargs, async/sync misuse, and typed field access errors.

**MCP-ready workflow 🔌**
> The CLI and MCP server share the same service layer. `fitz-forge` can run standalone, or sit behind Claude Desktop,
> Claude Code, Codex, or another MCP-capable client.

**Local-first execution 🏠**
> Ollama, LM Studio, and llama.cpp are supported local backends. Jobs are stored in SQLite and long runs can be resumed
> from checkpoints.

---

### Planning Artifact

`fitz-forge` treats the plan as the output contract.

It is not a guaranteed patch generator. The artifact code is intended to be concrete enough for a human or coding agent to
inspect, adapt, and implement.

<br>

| Part | Meaning | What you can do with it |
|------|---------|-------------------------|
| **Context** | Relevant repository files, structural summaries, interface signatures, and constraints. | See what evidence the plan is grounded in. |
| **Implementation status** | Whether the requested behavior already exists, with evidence and gaps. | Avoid reimplementing finished work or plan around the real missing pieces. |
| **Decisions** | Decomposed architectural decisions and resolved choices with rationale. | Review the design before code generation or agent handoff. |
| **Artifacts** | Proposed file-level code artifacts for critical implementation pieces. | Inspect concrete code shape and catch wrong interfaces early. |
| **Reviews** | Senior-engineer critique of assumptions, architecture, design, artifacts, and coverage. | Decide whether the plan is ready or needs another cycle. |
| **Roadmap** | Phased implementation steps, verification commands, and risk notes. | Hand the work to a coding agent or execute it manually. |

---

### Pipeline

[Pipeline Docs](docs/features/) • [Architecture](docs/features/reference/ARCHITECTURE.md) • [Configuration](docs/features/reference/CONFIG.md)

`fitz-forge` runs a 10-stage planning pipeline with deterministic code analysis between LLM calls.

The important design choice is that **a senior-engineer review layer wraps the pipeline**. It runs narrow critique passes
scoped to one stage's output, then regenerates the affected stage when a review flags issues.

<br>

```text
     USER PROMPT
          │
          ▼
┌─── 🧑‍💼 SENIOR ENGINEER REVIEW LAYER ───────────────────────────────┐
│   detect → regenerate → re-review → keep whichever is better      │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │                                                         │      │
│  │   1. Agent Context Gathering       [6-8 LLM]            │      │
│  │   2. Implementation Check          [1 LLM]              │      │
│  │   3. Call Graph Extraction         [0 · AST]            │      │
│  │   4. Decision Decomposition        [2-4 LLM]   ◂───────────────── decomposition review
│  │   5. Decision Resolution           [10-15 LLM]          │      │
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
```

<br>

| # | Stage | What it does | Docs |
|---|-------|--------------|------|
| 1 | Agent Context Gathering | Retrieve and compress codebase context. | [01_agent-context-gathering.md](docs/features/pipeline/01_agent-context-gathering.md) |
| 2 | Implementation Check | Ask whether the requested behavior already exists. | [02_implementation-check.md](docs/features/pipeline/02_implementation-check.md) |
| 3 | Call Graph Extraction | Build deterministic call graph context. | [03_call-graph-extraction.md](docs/features/pipeline/03_call-graph-extraction.md) |
| 4 | Decision Decomposition | Split the task into architectural decisions. | [04_decision-decomposition.md](docs/features/pipeline/04_decision-decomposition.md) |
| 5 | Decision Resolution | Resolve each decision against code evidence. | [05_decision-resolution.md](docs/features/pipeline/05_decision-resolution.md) |
| 6 | Synthesis | Build the plan shape and design. | [06_synthesis.md](docs/features/pipeline/06_synthesis.md) |
| 7 | Artifact Generation | Generate proposed code artifacts. | [07_artifact-generation.md](docs/features/pipeline/07_artifact-generation.md) |
| 8 | Grounding Validation | Check generated content against the repository. | [08_grounding-validation.md](docs/features/pipeline/08_grounding-validation.md) |
| 9 | Coherence Check | Check cross-stage consistency. | [09_coherence-check.md](docs/features/pipeline/09_coherence-check.md) |
| 10 | Render + Write | Write the final markdown plan. | - |

<br>

> [!NOTE]
> The current Python-heavy benchmark path is roughly 45-70 LLM calls and about 8-12 minutes on an RTX 5090. Hardware,
> model, source size, and optional review settings change that substantially.

---

### Evaluation

[Scorer V2 Spec](docs/features/reference/SCORER-V2-SPEC.md) • [Example Taxonomy](benchmarks/challenges/streaming_implementation/taxonomy.json) • [Golden-Plan Authoring Harness](docs/roadmap/golden-plan-authoring-harness.md)

The core evaluation problem is simple:

> How do you score a planning artifact when there is no single correct patch yet?

`fitz-forge` answers that with a task-specific golden-plan taxonomy. For each benchmark challenge, a frontier model such as
Claude Sonnet is used to inspect the target repository and draft the ideal planning artifact: the right architecture, the
files that matter, the expected implementation shape, and the common wrong approaches. That draft is reviewed into a
taxonomy before local-model output is scored.

<br>

| Taxonomy layer | What it captures |
|----------------|------------------|
| **Architecture tiers** | Whether the plan chooses the correct implementation shape or a degraded alternative. |
| **Required files** | Which files must be touched, which are optional, and which are distracting. |
| **Artifact criteria** | What each critical code artifact must contain. |
| **Failure modes** | Stubs, fabricated APIs, wrong request fields, missing streaming behavior, ungrounded imports, and similar defects. |
| **Verification shape** | Commands and checks that should prove the implementation works. |

Example architecture tiers for a streaming feature:

| Tier | Meaning | Score |
|------|---------|------:|
| `A1` | Full existing pipeline preserved; final generation streams tokens correctly. | 100 |
| `A2` | Streaming works but bypasses part of the synthesis abstraction. | 75 |
| `A3` | Calls a provider stream directly and skips retrieval/context logic. | 30 |
| `A4` | Calls the blocking answer path and splits the finished text. | 10 |
| `A5` | Gives up, stubs, or misses the feature. | 0 |

The scorer combines deterministic checks with taxonomy classification. Deterministic checks catch exact failures such as
missing required files, parse errors, `NotImplementedError`, `sys.stdout`, missing `yield`, fabricated symbols, unresolved
imports, cross-artifact method mismatches, and invalid roadmap commands. Taxonomy classification then maps the plan's
architecture and critical artifacts onto the golden answer key.

> [!NOTE]
> Plans generated during normal use do not carry a score. The scorer is an offline evaluation harness for measuring the
> planning pipeline and comparing raw local-model output against the same model inside the harness.

---

<details>

<summary><strong>📦 Quick Start</strong></summary>

<br>

#### Install

```bash
pip install fitz-forge
```

Optional extras:

```bash
pip install "fitz-forge[api-review]"  # Anthropic API review pass
pip install "fitz-forge[dev]"         # pytest, ruff, build tools
```

#### Configure

```bash
fitz-forge prep
```

Prerequisites:

- Python 3.10+
- one local LLM backend: [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), or [llama.cpp](https://github.com/ggerganov/llama.cpp)
- [fitz-sage](https://github.com/yafitzdev/fitz-sage) for code retrieval

#### Run

```bash
fitz-forge plan "Build a plugin system for data transformations"
```

Queue without running:

```bash
fitz-forge plan --detach "Build a plugin system for data transformations"
fitz-forge run
fitz-forge list
fitz-forge get <id>
```

#### Development install

```bash
git clone https://github.com/yafitzdev/fitz-forge.git
cd fitz-forge
pip install -e ".[dev]"
```

</details>

---

<details>

<summary><strong>📦 CLI Reference</strong></summary>

<br>

```bash
fitz-forge prep                 # Configure local model provider
fitz-forge plan "description"   # Create and run a planning job inline
fitz-forge plan --detach "..."  # Queue only
fitz-forge run                  # Process detached queued jobs
fitz-forge list                 # Show jobs
fitz-forge status <id>          # Check progress
fitz-forge get <id>             # Print completed plan
fitz-forge resume <id>          # Resume failed/interrupted job with live UI
fitz-forge retry <id>           # Re-queue failed/interrupted job
fitz-forge confirm <id>         # Approve optional API review
fitz-forge cancel <id>          # Skip optional API review
fitz-forge serve                # Start MCP server
```

Job lifecycle:

```text
QUEUED → RUNNING → COMPLETE
                  → AWAITING_REVIEW → QUEUED (confirm) / COMPLETE (cancel)
                  → FAILED / INTERRUPTED (both retryable)
```

</details>

---

<details>

<summary><strong>📦 MCP Usage</strong></summary>

<br>

For Claude Desktop, Claude Code, or another MCP-capable client:

```json
{
  "mcpServers": {
    "fitz-forge": {
      "command": "fitz-forge",
      "args": ["serve"]
    }
  }
}
```

Exposed MCP tools:

| Tool | Description |
|------|-------------|
| `create_plan` | Queue a planning job |
| `check_status` | Check job progress |
| `get_plan` | Retrieve a completed plan |
| `list_plans` | List planning jobs |
| `retry_job` | Retry a failed/interrupted job |
| `confirm_review` | Approve optional API review |
| `cancel_review` | Skip optional API review |

</details>

---

<details>

<summary><strong>📦 Architecture</strong> → <a href="docs/features/reference/ARCHITECTURE.md">Full Architecture Guide</a></summary>

<br>

```text
┌─────────────────────────────────────────────────────────────────┐
│                         fitz-forge                              │
├─────────────────────────────────────────────────────────────────┤
│  User Interfaces                                                │
│  CLI: prep | plan | run | list | status | get | serve           │
│  MCP: create_plan | check_status | get_plan | retry_job         │
├─────────────────────────────────────────────────────────────────┤
│  Shared Service Layer                                           │
│  fitz_forge/tools: CLI and MCP call the same operations          │
├─────────────────────────────────────────────────────────────────┤
│  Job Store                                                       │
│  SQLiteJobStore: queued jobs | state | checkpoints | plans       │
├─────────────────────────────────────────────────────────────────┤
│  Worker                                                         │
│  BackgroundWorker: sequential execution and crash recovery       │
├─────────────────────────────────────────────────────────────────┤
│  Planning Pipeline                                               │
│  retrieval | decisions | synthesis | artifacts | validation      │
├─────────────────────────────────────────────────────────────────┤
│  LLM Providers                                                   │
│  Ollama | LM Studio | llama.cpp | optional Anthropic review      │
└─────────────────────────────────────────────────────────────────┘
```

Main modules:

| Module | Role |
|--------|------|
| `fitz_forge/cli.py` | Typer CLI |
| `fitz_forge/server.py` | FastMCP server |
| `fitz_forge/tools/` | Shared service layer for CLI and MCP |
| `fitz_forge/models/` | SQLite job store and job state models |
| `fitz_forge/background/` | Worker lifecycle and crash recovery |
| `fitz_forge/llm/` | Ollama, LM Studio, llama.cpp clients |
| `fitz_forge/planning/` | Planning pipeline, reviews, artifacts, validation |
| `benchmarks/` | Internal evaluation harnesses and challenge tasks |

</details>

---

<details>

<summary><strong>📦 Configuration</strong> → <a href="docs/features/reference/CONFIG.md">Full Configuration Guide</a></summary>

<br>

`fitz-forge prep` creates a local config file and job database. The config selects the LLM backend, model, context length,
source directory behavior, output directory, and optional Anthropic review settings.

| Platform | Config path |
|----------|-------------|
| Windows | `%LOCALAPPDATA%\fitz-forge\fitz-forge\config.yaml` |
| macOS | `~/Library/Application Support/fitz-forge/config.yaml` |
| Linux | `~/.config/fitz-forge/config.yaml` |

Supported local providers are Ollama, LM Studio, and llama.cpp.

</details>

---

<details>

<summary><strong>📦 Boundaries / Troubleshooting</strong></summary>

<br>

**Is this a coding agent?**
> Not by itself. `fitz-forge` is a planning harness. It produces a grounded implementation plan and proposed artifacts;
> another coding agent or human still applies the patch.

**Are generated artifacts guaranteed to apply cleanly?**
> No. They are concrete planning artifacts, not a correctness guarantee. Closure and grounding checks catch important
> failure classes, but generated code still needs review and tests.

**Which codebases work best?**
> The current implementation is most mature on Python codebases. TypeScript-aware infrastructure exists, but should be
> treated as experimental.

**Do benchmark scores apply to normal plans?**
> No. Benchmark scores are offline evaluation signals. Normal planning jobs are not scored unless you run the benchmark
> harness.

**Why can a run take several minutes?**
> The pipeline intentionally breaks planning into many small LLM calls and review passes. That makes local models more
> reliable, but it is slower than one-shot prompting.

</details>

---

### Development 🧪

```bash
pytest
ruff check fitz_forge/
ruff format --check fitz_forge/ tests/
```

This is alpha software. Treat CI failures as blockers before publishing a release or using benchmark numbers as project
claims.

---

### License

MIT

---

### Links

- [GitHub](https://github.com/yafitzdev/fitz-forge)
- [PyPI](https://pypi.org/project/fitz-forge/)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

**Documentation:**
- [Feature Docs](docs/features/)
- [Architecture](docs/features/reference/ARCHITECTURE.md)
- [Configuration Reference](docs/features/reference/CONFIG.md)
- [Scorer V2 Spec](docs/features/reference/SCORER-V2-SPEC.md)
- [Senior Engineer Reviews](docs/features/infrastructure/senior-engineer-reviews.md)
- [Agent Context Gathering](docs/features/pipeline/01_agent-context-gathering.md)
- [Artifact Generation](docs/features/pipeline/07_artifact-generation.md)
- [Grounding Validation](docs/features/pipeline/08_grounding-validation.md)
- [Troubleshooting](docs/features/reference/TROUBLESHOOTING.md)
