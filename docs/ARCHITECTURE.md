# Architecture

fitz-forge is a local-first AI architectural planning system. Two interfaces (CLI + MCP) submit jobs to a shared service layer backed by a SQLite queue. A background worker picks jobs and runs them through the planning pipeline powered by a local LLM.

## System Diagram

```
CLI (typer)   ──→ tools/ ──→ SQLiteJobStore ←── BackgroundWorker ──→ DecomposedPipeline
MCP (fastmcp) ──→ tools/ ──→ SQLiteJobStore                              │
                                                                         ↓
                                                                   LLM Client
                                                              (Ollama / LM Studio / llama.cpp)
```

For the end-to-end pipeline flow see the [README diagram](../README.md#how-it-works) and the per-stage docs under [docs/features/pipeline/](features/pipeline/).

## Layer Dependencies

```
config/        ← no imports from planning/, tools/, cli, models/
models/        ← may import from config/
validation/    ← may import from models/
tools/         ← may import from models/, config/, validation/
llm/           ← may import from config/
planning/      ← may import from llm/, config/
background/    ← may import from models/, config/, planning/, llm/, tools/
cli/server     ← may import from all (user-facing layer)
```

Violation of layer dependencies blocks PRs. The intent: `config/` and `models/` are foundational — they never reach up into business logic.

## Module Responsibilities

### `config/` — Configuration

- `schema.py`: Pydantic models for all config (providers, agent, output)
- `loader.py`: YAML loader with platform-specific paths, auto-creation on first run

### `models/` — Data Layer

- `store.py`: Abstract `JobStore` interface
- `sqlite_store.py`: SQLite implementation with WAL mode, crash recovery
- `jobs.py`: `JobRecord` dataclass, state machine (QUEUED → RUNNING → COMPLETE)
- `responses.py`: Typed response models for CLI/MCP tools

### `tools/` — Service Layer

Shared by both CLI and MCP server. Each tool is a standalone async function:

| Tool | Description |
|------|-------------|
| `create_plan` | Validate inputs, generate job ID, queue work |
| `check_status` | Return job state, progress %, current phase |
| `get_plan` | Read completed plan markdown from disk |
| `list_plans` | List all jobs with state summary |
| `retry_job` | Re-queue failed/interrupted job |
| `confirm_review` | Approve optional API review pass |
| `cancel_review` | Skip API review, finalize plan |

### `llm/` — LLM Abstraction

Three provider backends behind a common interface:

- **Ollama** (`ollama.py`): Native client with OOM fallback
- **LM Studio** (`lm_studio.py`): OpenAI-compatible API, model switching via `lms` CLI
- **llama.cpp** (`llama_cpp.py`): Managed subprocess with flash attention, KV cache config, WDDM degradation detection

All providers expose `generate()`, `generate_with_tools()`, and `health_check()`.

### `planning/` — Pipeline Engine

The core of fitz-forge. See [docs/features/](features/) for per-stage breakdowns.

- `pipeline/orchestrator.py` — `DecomposedPipeline` (production) and `PlanningPipeline` (helper methods for implementation/coherence checks)
- `pipeline/stages/` — `DecisionDecompositionStage`, `DecisionResolutionStage`, `SynthesisStage`
- `pipeline/checkpoint.py` — SQLite-backed per-stage persistence
- `pipeline/call_graph.py` — deterministic call graph extraction from AST
- `agent/gatherer.py` — fitz-sage retrieval bridge with planning-specific post-processing
- `agent/compressor.py` — test body collapse, non-Python comment stripping
- `agent/indexer.py` — interface/library signature extraction
- `artifact/` — per-artifact code generation with type-aware closure checks
- `validation/grounding/` — AST + LLM validation of generated artifacts

### `background/` — Worker

- `worker.py`: Polls SQLite queue, runs pipeline, writes markdown output
- `lifecycle.py`: Worker startup/shutdown, PID tracking
- `signals.py`: Graceful shutdown on SIGINT/SIGTERM

### `api_review/` — Optional Anthropic Review

- `client.py`: Sends flagged sections to Claude for review
- `cost_calculator.py`: Estimates API cost before user approval
- `schemas.py`: Review request/response types

## Job State Machine

```
QUEUED → RUNNING → COMPLETE
                 → AWAITING_REVIEW → QUEUED (confirm) / COMPLETE (cancel)
                 → FAILED (retryable)
                 → INTERRUPTED (retryable — crash recovery)
```

State transitions are atomic (SQLite WAL mode). The `RUNNING → INTERRUPTED` transition happens automatically when the worker detects a job was left in RUNNING state from a previous crash.

## Key Design Decisions

1. **Sequential stages, not parallel.** Each stage needs outputs from all prior stages. Parallelism happens inside stages (per-decision resolution runs sequentially to honor topological order; per-artifact generation can overlap).

2. **Decompose, don't monolith.** The hardest reasoning step — the full architectural plan — is never attempted in one LLM call. Decision decomposition breaks the task into 10-15 small decisions; synthesis turns pre-solved decisions into prose; per-field extraction converts prose into typed JSON. Each step is small enough for a 3B quantized model.

3. **Checkpoints after every stage.** A plan takes several minutes on local hardware. Losing progress to a crash is unacceptable.

4. **Deterministic scaffolding around LLM work.** Call graph, structural index, grounding AST check, closure check, type-aware repair — all deterministic. LLMs only do what LLMs are good at (prose, fuzzy matching, small structured extraction).

5. **fitz-sage for retrieval.** Code retrieval is a solved problem in fitz-sage (89% critical recall). No reason to maintain a second retrieval engine.
