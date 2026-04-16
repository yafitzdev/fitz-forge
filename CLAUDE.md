## Rules

1. **File path comment required** - First line: `# fitz_forge/path/to/file.py`
2. **No stdout** - MCP uses stdio. All logging → stderr via `logging`. Never `print()`.
3. **Always use .venv** - `.venv/Scripts/pip` (Windows) or `.venv/bin/pip` (Unix)
4. **No legacy code** - No backwards compat, no shims. Delete completely when removing.
5. **Codebase agnostic** - No fitz_ai-specific assumptions in tools/. All codebase-specific logic must be in the MCP or CLI layers.
6. **No threshold tuning** - If we run into a problem, fix the underlying issue instead of tuning thresholds. For example, if the LLM fails to extract a field, fix the prompt or extraction method instead of adding a "confidence threshold" that lets it return an empty value.
7. **No implementation without consulting me first** - If you think a change is needed, or if you're not sure about something, first propose the change and discuss it with me before implementing. Small changes are fine to implement directly, but for anything non-trivial, let's align on the approach first. This is especially important for changes that affect the LLM prompts, pipeline structure, or job handling logic. We want to avoid unnecessary work and ensure we're making the right improvements.
8. **Ask yourself periodically** - "Do I need to update any documentation?". 
9. **Benchmarking** - When you run the benchmark, consult benchmarks/BENCHMARK.md for instructions. Also make sure to always check the benchmark result after the first run, to validate that it's working correctly. If you see any anomalies in the scores, investigate before proceeding with further changes.
10. **Fix invariants, not symptoms** - Before patching a bug, ask: "what invariant is being violated here, and does my fix enforce it for every variant or only the one I just saw?" If a fix only works for the specific failure in front of you, it's a symptom patch and the next variant will slip through. State the underlying property the system must satisfy, then enforce it. Example: "the route calls `service.query_stream` which doesn't exist" is a symptom; "every cross-file reference in an artifact must be satisfied by the codebase or a sibling artifact" is the invariant. Patches to the specific failure breed whack-a-mole; enforcing the invariant catches the whole class.
11. **Watch for set-level bugs** - If validation looks at things one at a time (one artifact, one stage, one call) but the bug only exists at the set level (inconsistency across siblings, missing dependency spanning files, closure violations), no amount of per-item checking will catch it. When you notice this shape, lift the check to operate on the whole set, not the individual item. Ask: "is this property a property of the item, or of the set?" — and put the check at the right level.

## The Fixer Loop

Autonomous benchmark-improvement cycle. Use when plan quality needs
to go up on a specific task. Every fix must be **codebase and programming
language agnostic** — no task-specific hacks.

### Setup

1. Pick a target codebase + task description
2. Create `benchmarks/<task>/taxonomy.json` (architecture tiers + per-file quality tiers)
3. Create `benchmarks/<task>/ideal_context.json` (file_list of ~30 relevant files)
4. Create `docs/v2-scoring/<task>/BUG_REGISTER.md`
5. Run a baseline benchmark: `plan_factory decomposed --runs 10 --taxonomy <path>`

### Loop (each cycle)

1. **Score** — compute deterministic scores using the task's taxonomy
2. **Triage** — read all failure patterns from the run, add to BUG_REGISTER.md with impact scores (1–10)
3. **Pick** — select the single highest-impact open bug
4. **Fix** — implement the fix, generalized to every variant of the failure shape (rule 10). Ask: "does this fix apply to any codebase/language, or is it specific to fitz-sage?" If specific, don't ship it
5. **Replay-validate** — replay a snapshot from the baseline run (~5 min). Only promote to full benchmark if replay shows improvement
6. **Regression-check** — re-score ALL previous task benchmarks with the new code. If any regress, revert
7. **Mark done** — update BUG_REGISTER.md, loop to step 1

### Exit criteria

- 10 runs with 90+ average, at most 1 dud below 90
- OR: all open bugs have impact ≤ 3 and no fix is available without regressing other tasks

### Commands

```bash
# Baseline run
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
    --runs 10 --source-dir <codebase> \
    --context-file benchmarks/<task>/ideal_context.json \
    --query "<task description>" \
    --taxonomy benchmarks/<task>/taxonomy.json \
    --score-v2

# Replay (fast validation, ~5 min)
.venv/Scripts/python -m benchmarks.plan_factory replay \
    --snapshot benchmarks/results/<run>/traces_01/snapshot_after_decision_resolution.json \
    --source-dir <codebase> \
    --context-file benchmarks/<task>/ideal_context.json \
    --query "<task description>" \
    --score-v2

# Manual scoring with correct taxonomy
python -c "
from benchmarks.eval_v2_deterministic import run_deterministic_checks
from benchmarks.eval_v2_taxonomy import load_taxonomy
tax = load_taxonomy(Path('benchmarks/<task>/taxonomy.json'))
r = run_deterministic_checks(plan, structural_index='',
    task_requires_streaming=False,
    taxonomy_files=tax.required_files,
    source_dir='<codebase>')
print(r.deterministic_score)
"
```

### Track record

| Task | Codebase | Language | Baseline | After loop | Runs |
|------|----------|----------|----------|------------|------|
| Streaming | fitz-sage | Python | 68.85 | 97.70 | 30 |
| Ranking explanations | fitz-sage | Python | 68.85 | 97.08 | 10 |

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

### LLM Call Quality Layer

All LLM calls go through `fitz_forge/llm/generate.py:generate()` — never call `client.generate()` directly. This function provides:
- **Budget capping**: `max_tokens = min(requested, context_size - prompt_tokens - 512)`
- **Output sanitization**: quadruple docstrings, unicode artifacts
- **Truncation detection + retry**: unclosed code fences, bracket imbalance, unclosed JSON strings
- **Provenance tracing**: `configure_tracing(trace_dir)` enables JSON traces per call

### Artifact Generation Black Box

All artifact generation goes through `fitz_forge/planning/artifact/`. Two entry points:

**Per-artifact:** `generate_artifact(filename, purpose, ctx, ...)` — produces one validated artifact.
- **Input assembly** (`context.py`): gathers source, interfaces, reference methods deterministically
- **Strategy selection**: `SurgicalRewriteStrategy` if reference method exists (default), `NewCodeStrategy` for genuinely new files
- **Raw code output**: model outputs Python directly — no JSON wrapping, no quote mangling
- **Validation** (`validate.py`): parseable, no fabrication, has yield (streaming), correct return type
- **Retry with feedback**: up to 3 attempts, each retry includes specific error messages from validation

**Batch:** `generate_artifact_set(specs, ctx, ...)` — produces a *closed* artifact set. This is the primary entry point synthesis uses.
- Calls `generate_artifact` per spec (unchanged), accumulating method signatures for consistency
- After all artifacts are generated, runs the **closure family of checks** (`closure.py`) — five invariants on the set:
  1. **Existence** — every cross-file symbol must be satisfied by codebase or sibling artifact
  2. **Usage** — `async for` only on async iterators; `await` only on coroutines; `for` not on async iterables
  3. **Kwargs** — every keyword argument name must be a parameter of the callee
  4. **Imports** — `from pkg.mod import X` → X must resolve in codebase or siblings
  5. **Field access** — `obj.field` on a typed local → field must exist on that type
- **Type tracking** that powers all five: function param annotations, `var = ClassName(...)`, service locator return types (`var = get_service()`), and `self._attr` types parsed from the target class's `__init__` in disk source
- **Repair strategies**:
  - *Strategy 1 (expand)* — for missing symbols, add a new sibling artifact (e.g. `services/fitz_service.py` when a route calls `service.query_stream()` that never existed)
  - *Strategy 2 (regenerate)* — for usage/kwargs/field violations, regenerate the offending artifact with specific feedback
- Returns `ArtifactSetResult` with `closed=True/False` and any remaining violations
- Closure is an **invariant on the set**, not on any individual artifact — per-artifact validation alone can never catch cross-file inconsistency (see rule 11)

