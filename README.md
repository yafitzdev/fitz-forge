<div align="center">

# fitz-forge

### Experimental coding-plan harness for local LLMs

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/fitz-forge.svg)](https://pypi.org/project/fitz-forge/)
[![Tests](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml/badge.svg)](https://github.com/yafitzdev/fitz-forge/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/version-0.6.2-green.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[Research Question](#research-question) | [Current Answer](#current-answer) | [Why fitz-forge?](#why-fitz-forge) | [How It Works](#how-it-works) | [Evaluation](#evaluation) | [Quick Start](#quick-start) | [Docs](docs/features/)

</div>

---

## Status

`fitz-forge` is an alpha-stage research/engineering project. The project asks a narrow question:

> How much coding-planning ability can be moved out of the model and into the harness around it?

The current implementation generates coding-plan artifacts from a real codebase using local LLMs. The interesting part is the harness: retrieval, task decomposition, structured schemas, deterministic AST checks, artifact generation, and review/regeneration loops.

## Research Question

Can a local LLM, when wrapped in the right planning harness, produce coding plans that are substantially more complete, grounded, and actionable than the same model prompted directly?

In other words:

> Can scaffolding compensate for model weakness in agentic coding planning?

This project treats local models as a stress test. If a weak/local model improves significantly under a harness, that improvement is evidence that at least part of "agentic intelligence" lives in the system design, not only in model weights.

## Current Answer

The current answer is:

> Yes, substantially for planning. Not enough to replace frontier coding agents end-to-end.

What the project has shown so far on curated internal challenge tasks:

- Raw local models tend to produce vague, partial, or hallucinated coding plans.
- The same class of model becomes much more useful when the task is decomposed into small decisions and each stage is checked.
- Deterministic scaffolding matters: call graphs, structural indexes, grounding checks, closure checks, and set-level artifact validation catch failures a prompt alone misses.
- Review/regeneration loops matter: scoped "senior engineer" critique passes can improve stage outputs without relying on one giant self-critique prompt.
- Token savings are not the core result. Claude Code and similar tools already use caching and still read code during implementation. The stronger direction is plan quality and, next, plan executability.

The next research step is to measure whether harness-produced plans reduce downstream implementation adaptation: fewer missing files, fewer hallucinated APIs, fewer corrective turns, and eventually higher patch-resolution rates on external benchmarks.

## Shape of the Difference

Illustrative example, not a benchmark result:

| Direct local prompt | Same model inside the harness |
|---|---|
| "Add a WebSocket endpoint. Use the websockets library. Create handlers. Add auth middleware." | "Extend `api/routes/chat.py`; reuse the existing chat service; validate tokens through the existing auth layer; add tests under `tests/api/`; verify with `pytest ...`." |
| Generic advice. No file paths. Easy to hallucinate libraries and miss repository conventions. | Real files, explicit dependencies, phased work, and verification commands grounded in the codebase. |

## What It Produces

`fitz-forge` produces a markdown plan with structured sections such as:

- relevant codebase context,
- implementation status checks,
- decomposed architectural decisions,
- resolved decisions with evidence,
- proposed code artifacts,
- phased roadmap and verification commands,
- risk notes and review findings.

The artifact code is intended to be concrete enough for a human or coding agent to inspect and adapt. It is not yet guaranteed to be directly applicable as a patch.

## How It Works

A 10-stage pipeline decomposes architectural planning into small, focused LLM calls interleaved with deterministic AST work. Retrieval and implementation checks feed a decision-based reasoning core: decompose, resolve, synthesize. Artifacts are then generated, closure-checked, and grounded against the real codebase before the plan is written.

The important design choice is that **a senior-engineer review layer wraps the pipeline**. It runs narrow critique passes scoped to one stage's output, then regenerates the affected stage when a review flags issues.

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

Total: ~45-70 LLM calls · ~8-12 min on RTX 5090 for the current Python-heavy benchmark path.
```

| # | Stage | Docs |
|---|---|---|
| 1 | Agent Context Gathering | [01_agent-context-gathering.md](docs/features/pipeline/01_agent-context-gathering.md) |
| 2 | Implementation Check | [02_implementation-check.md](docs/features/pipeline/02_implementation-check.md) |
| 3 | Call Graph Extraction | [03_call-graph-extraction.md](docs/features/pipeline/03_call-graph-extraction.md) |
| 4 | Decision Decomposition | [04_decision-decomposition.md](docs/features/pipeline/04_decision-decomposition.md) |
| 5 | Decision Resolution | [05_decision-resolution.md](docs/features/pipeline/05_decision-resolution.md) |
| 6 | Synthesis | [06_synthesis.md](docs/features/pipeline/06_synthesis.md) |
| 7 | Artifact Generation | [07_artifact-generation.md](docs/features/pipeline/07_artifact-generation.md) |
| 8 | Grounding Validation | [08_grounding-validation.md](docs/features/pipeline/08_grounding-validation.md) |
| 9 | Coherence Check | [09_coherence-check.md](docs/features/pipeline/09_coherence-check.md) |
| 10 | Render + Write | - |
| * | Senior Engineer Reviews | [senior-engineer-reviews.md](docs/features/infrastructure/senior-engineer-reviews.md) |

The pipeline decomposes a problem that would overwhelm a small model into calls small enough to review, validate, and retry. Deterministic AST work carries the structural load so LLM calls can focus on interpretation and design.

The senior-engineer review layer is the quality multiplier. A local model writing a plan unsupervised tends to pick plausible wrong patterns, under-specify interfaces, and build on assumptions the codebase contradicts. Each review is one narrow critique that hands feedback back to the stage for regeneration. Reviews are fail-safe: they can improve the plan or leave it unchanged.

Full pipeline documentation lives in [docs/features/](docs/features/).

## Architecture

```text
CLI (typer)   -> tools/ -> SQLiteJobStore <- BackgroundWorker -> DecomposedPipeline
MCP (fastmcp) -> tools/ -> SQLiteJobStore                              |
                                                                       v
                                                                 LLM Client
                                                        Ollama / LM Studio / llama.cpp
```

Main modules:

| Module | Role |
|---|---|
| `fitz_forge/cli.py` | Typer CLI |
| `fitz_forge/server.py` | FastMCP server |
| `fitz_forge/tools/` | Shared service layer for CLI and MCP |
| `fitz_forge/models/` | SQLite job store and job state models |
| `fitz_forge/background/` | Worker lifecycle and crash recovery |
| `fitz_forge/llm/` | Ollama, LM Studio, llama.cpp clients |
| `fitz_forge/planning/` | Planning pipeline, reviews, artifacts, validation |
| `benchmarks/` | Internal evaluation harnesses and challenge tasks |

See [docs/features/reference/ARCHITECTURE.md](docs/features/reference/ARCHITECTURE.md) for the full architecture guide.

## Evaluation

The core evaluation question is: **how do you score a planning artifact when there is no single correct patch yet?**

`fitz-forge` answers this with a task-specific golden-plan taxonomy. For each benchmark challenge, a frontier model such as Claude Sonnet is used to inspect the target repository and draft the ideal planning artifact: the right architecture, the files that matter, the expected implementation shape, and the common wrong approaches. That draft is then reviewed into a taxonomy before any local-model output is scored.

The taxonomy defines what a good plan must understand:

- which files are required, recommended, or optional,
- what the ideal architecture looks like,
- which degraded architecture patterns are acceptable, partial, poor, or failing,
- what a correct implementation artifact should contain for each critical file,
- which failure modes should be penalized, such as stubs, fabricated APIs, missing streaming behavior, wrong request fields, or ungrounded imports.

That taxonomy becomes the benchmark's answer key. A generated plan is not graded by vibes; it is classified against explicit tiers.

Example shape for a streaming feature:

| Tier | Meaning | Score |
|---|---|---:|
| `A1` | Full existing pipeline preserved; final generation streams tokens correctly | 100 |
| `A2` | Streaming works but bypasses part of the synthesis abstraction | 75 |
| `A3` | Calls a provider stream directly and skips retrieval/context logic | 30 |
| `A4` | Calls the blocking answer path and splits the finished text | 10 |
| `A5` | Gives up, stubs, or misses the feature | 0 |

The same idea applies per file. For example, `engine.py` might have tiers for "full pipeline with `yield` and correct return type," "partial pipeline," "direct provider shortcut," "blocking implementation," and "absent/stubbed." Routes, schemas, SDK files, and services each get their own task-specific tiers when they matter.

The scorer then combines two layers:

- **Deterministic checks** verify things a program can know exactly: required-file coverage, parseability, `NotImplementedError`, `sys.stdout`, missing `yield`, fabricated methods/classes/fields, unresolved imports, cross-artifact method mismatches, and roadmap verification commands.
- **Taxonomy classification** maps the plan's architecture and critical artifacts onto the golden taxonomy entries. A model can help classify, but the scores come from the prewritten taxonomy, not from an open-ended opinion.

This makes the benchmark useful for research: a raw local model and the same model inside the harness can be compared against the same golden taxonomy. The question becomes measurable: did the harness move the plan toward the ideal architecture, required file coverage, grounded artifacts, and executable roadmap?

Plans generated during normal use do not carry a score. The scorer is an offline evaluation harness for measuring the planning pipeline. See [docs/features/reference/SCORER-V2-SPEC.md](docs/features/reference/SCORER-V2-SPEC.md) for the detailed scoring design, [benchmarks/challenges/streaming_implementation/taxonomy.json](benchmarks/challenges/streaming_implementation/taxonomy.json) for a concrete taxonomy, and [docs/roadmap/golden-plan-authoring-harness.md](docs/roadmap/golden-plan-authoring-harness.md) for the planned Sonnet-assisted authoring tool.

## Quick Start

Prerequisites:

- Python 3.10+
- one local LLM backend: [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), or [llama.cpp](https://github.com/ggerganov/llama.cpp)
- [fitz-sage](https://github.com/yafitzdev/fitz-sage) for code retrieval

Install:

```bash
pip install fitz-forge
```

Optional extras:

```bash
pip install "fitz-forge[api-review]"  # Anthropic API review pass
pip install "fitz-forge[dev]"         # pytest, ruff, build tools
```

First-run configuration:

```bash
fitz-forge prep
```

Create and run a plan:

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

Development install:

```bash
git clone https://github.com/yafitzdev/fitz-forge.git
cd fitz-forge
pip install -e ".[dev]"
```

## Common CLI Commands

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
QUEUED -> RUNNING -> COMPLETE
                  -> AWAITING_REVIEW -> QUEUED (confirm) / COMPLETE (cancel)
                  -> FAILED / INTERRUPTED (both retryable)
```

## MCP Usage

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
|---|---|
| `create_plan` | Queue a planning job |
| `check_status` | Check job progress |
| `get_plan` | Retrieve a completed plan |
| `list_plans` | List planning jobs |
| `retry_job` | Retry a failed/interrupted job |
| `confirm_review` | Approve optional API review |
| `cancel_review` | Skip optional API review |

## Configuration

`fitz-forge prep` creates a local config file and job database. The config selects the LLM backend, model, context length, source directory behavior, output directory, and optional Anthropic review settings.

| Platform | Config path |
|---|---|
| Windows | `%LOCALAPPDATA%\fitz-forge\fitz-forge\config.yaml` |
| macOS | `~/Library/Application Support/fitz-forge/config.yaml` |
| Linux | `~/.config/fitz-forge/config.yaml` |

Supported local providers are Ollama, LM Studio, and llama.cpp. See [docs/features/reference/CONFIG.md](docs/features/reference/CONFIG.md) for every field.

## Limitations

- Not a general autonomous coding agent.
- Not a guaranteed patch generator.
- Not a replacement for Claude Code, Codex, or human review.
- Most mature on Python codebases; TypeScript-aware infrastructure exists but should be treated as experimental.
- Benchmark scores are offline research signals, not correctness guarantees for normal user plans.

## Development

```bash
pytest
ruff check fitz_forge/
ruff format --check fitz_forge/ tests/
```

This is alpha research code. Treat CI failures as blockers before publishing a release or using benchmark numbers as project claims.

## Links

- [Architecture](docs/features/reference/ARCHITECTURE.md)
- [Feature Docs](docs/features/)
- [Configuration Reference](docs/features/reference/CONFIG.md)
- [Troubleshooting](docs/features/reference/TROUBLESHOOTING.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT
