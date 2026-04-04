# Architecture

fitz-forge is a local-first AI architectural planning system. Two interfaces (CLI + MCP) submit jobs to a shared service layer backed by a SQLite queue. A background worker picks jobs and runs them through a multi-stage planning pipeline powered by local LLMs.

## System Diagram

```
CLI (typer)   ──→ tools/ ──→ SQLiteJobStore ←── BackgroundWorker ──→ PlanningPipeline
MCP (fastmcp) ──→ tools/ ──→ SQLiteJobStore                              │
                                                                          ↓
                                                                   LLM Client
                                                              (Ollama / LM Studio / llama.cpp)
```

## Layer Dependencies

```
config/        ← NO imports from planning/, tools/, cli, models/
models/        ← May import from config/
validation/    ← May import from models/
tools/         ← May import from models/, config/, validation/
llm/           ← May import from config/
planning/      ← May import from llm/, config/
background/    ← May import from models/, config/, planning/, llm/, tools/
cli/server     ← May import from all (user-facing layer)
```

Violation of layer dependencies blocks PRs. The intent: `config/` and `models/` are foundational — they never reach up into business logic.

## Module Responsibilities

### `config/` — Configuration

- `schema.py`: Pydantic models for all config (providers, agent, confidence, output)
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

- **Ollama** (`ollama.py`): Native client with OOM fallback (80B → 32B)
- **LM Studio** (`lm_studio.py`): OpenAI-compatible API, model switching via `lms` CLI
- **llama.cpp** (`llama_cpp.py`): Managed subprocess with flash attention, KV cache config, WDDM degradation detection

All providers expose `generate()`, `generate_with_tools()`, and `health_check()`.

### `planning/` — Pipeline Engine

The core of fitz-forge. See [docs/features/](features/) for detailed breakdowns.

**Two pipeline variants:**

1. **PlanningPipeline** (original): Context → Architecture+Design → Roadmap+Risk
2. **DecomposedPipeline** (v0.5+): Decision Decomposition → Decision Resolution → Synthesis

Both share: agent context gathering, implementation check, checkpointing, coherence check, confidence scoring.

**Sub-modules:**

- `pipeline/orchestrator.py`: Sequential stage execution, checkpoint recovery, coherence check
- `pipeline/stages/`: Stage implementations (each extends `PipelineStage`)
- `pipeline/checkpoint.py`: SQLite-backed per-stage persistence
- `pipeline/validators.py`: Post-extraction validators (ensure file paths, ADRs, verification commands)
- `pipeline/call_graph.py`: Deterministic call graph extraction from AST
- `agent/gatherer.py`: fitz-sage retrieval bridge with planning-specific post-processing
- `agent/compressor.py`: Test body collapse, non-Python comment stripping
- `agent/indexer.py`: Interface/library signature extraction
- `confidence/scorer.py`: Hybrid LLM + heuristic quality scoring
- `validation/grounding.py`: AST + LLM artifact grounding validation

### `background/` — Worker

- `worker.py`: Polls SQLite queue, runs pipeline, writes markdown output
- `lifecycle.py`: Worker startup/shutdown, PID tracking
- `signals.py`: Graceful shutdown on SIGINT/SIGTERM

### `api_review/` — Optional Anthropic Review

- `client.py`: Sends low-confidence sections to Claude for review
- `cost_calculator.py`: Estimates API cost before user approval
- `schemas.py`: Review request/response types

## Data Flow

```
User → "fitz plan 'Add OAuth2'" → create_plan tool → SQLiteJobStore (QUEUED)
                                                            │
BackgroundWorker polls ─────────────────────────────────────┘
    │
    ├── AgentContextGatherer (fitz-sage retrieval)
    │     └── structural index → LLM scan → import expand → compress
    │
    ├── Implementation check (is this already built?)
    │
    ├── Stage 1: Context (requirements, constraints, assumptions)
    ├── Stage 2: Architecture + Design (approaches, ADRs, components, artifacts)
    ├── Stage 3: Roadmap + Risk (phases, critical path, risk register)
    │
    ├── Cross-stage coherence check
    ├── Confidence scoring (per-section, 1-10 scale)
    ├── Optional API review pause (AWAITING_REVIEW state)
    │
    └── Render markdown → write to ~/.fitz-forge/plans/ → mark COMPLETE
```

## Job State Machine

```
QUEUED → RUNNING → COMPLETE
                 → AWAITING_REVIEW → QUEUED (confirm) / COMPLETE (cancel)
                 → FAILED (retryable)
                 → INTERRUPTED (retryable — crash recovery)
```

State transitions are atomic (SQLite WAL mode). The `RUNNING → INTERRUPTED` transition happens automatically when the worker detects a job was left in RUNNING state from a previous crash.

## Key Design Decisions

1. **Sequential stages, not parallel**: Each stage needs outputs from all prior stages. Parallelism happens within stages (verification agents, field group extraction).

2. **Per-field extraction over monolithic JSON**: Small models can't reliably produce large JSON. Breaking extraction into <2000-char schemas makes 3B quantized models work.

3. **Checkpoints after every stage**: A plan takes 30-60 minutes on local hardware. Losing progress to a crash is unacceptable.

4. **Two pipeline variants**: The original 3-stage pipeline is simpler but limited. The decomposed pipeline (decision-based) produces better plans but uses more LLM calls.

5. **fitz-sage for retrieval**: Code retrieval is a solved problem in fitz-sage (89% critical recall). No reason to maintain a second retrieval engine.
