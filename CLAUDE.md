## Rules

1. **File path comment required** - First line: `# fitz_forge/path/to/file.py`
2. **No stdout** - MCP uses stdio. All logging → stderr via `logging`. Never `print()`.
3. **Always use .venv** - `.venv/Scripts/pip` (Windows) or `.venv/bin/pip` (Unix)
4. **No legacy code** - No backwards compat, no shims. Delete completely when removing.

## What This Is

Local-first AI architectural planning via local LLMs (Ollama or LM Studio). Two interfaces (CLI + MCP) wrap the same `tools/` service layer. Background worker processes jobs sequentially from SQLite queue.

```
CLI (typer)    ──→ tools/ ──→ SQLiteJobStore ←── BackgroundWorker ──→ PlanningPipeline
MCP (fastmcp)  ──→ tools/ ──→ SQLiteJobStore
```

## Quick Reference

```bash
pip install -e ".[dev]"           # Dev install
pytest                            # 402 tests
fitz-forge plan "desc"        # Queue job
fitz-forge run                # Start worker (Ctrl+C to stop)
fitz-forge list               # Show all jobs
fitz-forge status <id>        # Check progress
fitz-forge get <id>           # Print plan markdown
fitz-forge retry <id>         # Re-queue failed job
fitz-forge confirm <id>       # Approve API review
fitz-forge cancel <id>        # Skip API review
fitz-forge serve              # Start MCP server
```

## Job States

```
QUEUED → RUNNING → COMPLETE
                 → AWAITING_REVIEW → QUEUED (confirm) / COMPLETE (cancel)
                 → FAILED / INTERRUPTED (both retryable)
```

## Pipeline (agent pre-stage + 3 planning stages, sequential)

0. **Agent context gathering** (0.06-0.09) — Multi-pass pipeline (map → index → navigate → summarize → synthesize), no tool calling. Python builds a structural index (classes, functions, imports), LLM navigates by keywords to pick relevant files, summarizes each, and synthesizes into context doc. Returns `{"synthesized": str, "raw_summaries": str}`. Orchestrator injects both into `prior_outputs`. Checkpointed — skipped on resume.
0.5. **Implementation check** (0.092) — Surgical LLM call: "is this task already implemented?" Returns `{"already_implemented": bool, "evidence": str, "gaps": [str]}`. Injected into `prior_outputs["_implementation_check"]` so downstream stages start from ground truth.
1. **Context** (0.10-0.25) — requirements, constraints, assumptions. Per-field extraction (4 groups).
2. **Architecture+Design** (0.25-0.65) — merged stage, per-field extraction (6 groups). Returns `{"architecture": {...}, "design": {...}}`, flattened into `prior_outputs`.
3. **Roadmap+Risk** (0.65-0.95) — merged stage, per-field extraction (3 groups). Returns `{"roadmap": {...}, "risk": {...}}`.

Per-field extraction: 1 reasoning + 1 self-critique + N small JSON extractions per stage. Each extraction produces a tiny schema (<2000 chars) that a 3B model can handle reliably. Failed groups get Pydantic defaults instead of crashing the stage. Selective krag_context: only groups needing codebase evidence receive it.

Post-pipeline: cross-stage coherence check → confidence scoring (section-specific criteria, 1-10 scale) → optional API review pause → render markdown → write file.

