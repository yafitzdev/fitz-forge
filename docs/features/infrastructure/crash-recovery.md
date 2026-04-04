# Crash Recovery

## Problem

Planning runs take 30-60 minutes on local hardware (consumer GPU running 3B-35B
quantized models). A power outage, system crash, or accidental terminal close
at minute 55 loses all completed work. Without persistence, the user must
restart the entire pipeline from scratch -- including the expensive agent context
gathering phase that reads and summarizes the target codebase.

## Solution

SQLite-backed checkpoints after every pipeline stage. Completed stage outputs
are persisted to the `jobs.pipeline_state` column as JSON. On resume, the
pipeline loads the checkpoint, skips completed stages, and continues from the
next incomplete stage. No work is repeated.

## How It Works

### CheckpointManager

`CheckpointManager` in `pipeline/checkpoint.py` provides three operations:

- **`save_stage(job_id, stage_name, stage_output)`** -- persists a completed
  stage's parsed output to SQLite. Uses `BEGIN IMMEDIATE` for an exclusive lock
  during the write, preventing concurrent workers from corrupting the checkpoint.

- **`load_checkpoint(job_id)`** -- loads all completed stage outputs for a job.
  Returns a dict mapping `stage_name -> stage_output`. Returns empty dict if no
  checkpoint exists.

- **`clear_checkpoint(job_id)`** -- clears all checkpoint data for a job. Used
  when starting a fresh planning run (not resuming) to avoid stale data from a
  previous attempt.

### Checkpoint Data Format

Each checkpoint entry in the `pipeline_state` JSON column uses the timestamped
format:

```json
{
  "context": {
    "output": {"project_description": "...", "key_requirements": [...]},
    "completed_at": "2026-04-04T12:34:56+00:00"
  },
  "architecture_design": {
    "output": {"architecture": {...}, "design": {...}},
    "completed_at": "2026-04-04T12:45:00+00:00"
  }
}
```

The `completed_at` timestamp enables stale checkpoint detection.
`load_checkpoint()` logs a warning when any stage checkpoint is older than 24
hours, alerting the user that the resumed context may be outdated.

Legacy checkpoints (plain dict without the `output`/`completed_at` wrapper) are
supported transparently -- `load_checkpoint()` detects the format and unwraps
accordingly.

### Agent Context Checkpoint

The agent context gathering phase (stage 0) is also checkpointed under the key
`_agent_context`. This is significant because agent context gathering involves
multiple LLM calls (query expansion, structural scan, summarization, synthesis)
and can take 5-10 minutes. On resume, the gathered context is loaded from the
checkpoint and injected into `prior_outputs` without re-running any of the
agent pipeline.

Internal state keys (prefixed with `_`) are excluded from the stage completion
count that drives progress reporting. This prevents the progress bar from
showing inflated completion percentages due to internal checkpoints.

### Resume Flow

When a job is resumed (`PlanningPipeline.execute(resume=True)`):

1. `load_checkpoint(job_id)` retrieves all completed stage outputs.
2. For each pipeline stage, if its name exists in the checkpoint, the stage is
   skipped and its output is used as-is in `prior_outputs`.
3. The progress callback reports completed stages as done (the UI shows them
   green immediately rather than replaying them).
4. Execution resumes from the first stage not present in the checkpoint.
5. New stage completions are checkpointed normally.

### Job State Machine on Crash

The background worker manages job state transitions around crashes:

- **Normal flow**: `QUEUED -> RUNNING -> COMPLETE`
- **Crash during processing**: The job remains in `RUNNING` state in the
  database (the worker did not get a chance to update it).
- **Worker restart**: On startup, the worker scans for jobs in `RUNNING` state
  and transitions them to `INTERRUPTED`. This state is retryable.
- **User retries**: `fitz-forge retry <id>` transitions `INTERRUPTED -> QUEUED`.
  When the worker picks up the job, it detects the existing checkpoint and
  resumes from where it left off.

### Graceful Shutdown

When the worker receives a shutdown signal (Ctrl+C):

1. The `asyncio.CancelledError` is caught in the processing loop.
2. The current job is transitioned to `INTERRUPTED` state with the error
   message `"Server shutdown during processing"`.
3. The checkpoint from the last completed stage is preserved.
4. On next `fitz-forge retry <id>`, the job resumes from checkpoint.

### Checkpoint Integrity

`save_stage()` uses `BEGIN IMMEDIATE` (SQLite exclusive transaction) to ensure
atomicity. If the process crashes mid-write:

- SQLite's journal/WAL mode ensures the incomplete transaction is rolled back
  on next open.
- The checkpoint retains the state from the previous successful `save_stage()`
  call.
- At worst, one stage's work is lost (the one that was being written during
  crash), not the entire pipeline.

## Key Design Decisions

1. **SQLite over filesystem checkpoints** -- the job database already exists and
   uses SQLite. Adding a column is simpler and more atomic than managing separate
   checkpoint files with their own consistency guarantees.

2. **Per-stage granularity, not per-extraction** -- checkpointing after each of
   the 3 pipeline stages (not after each of the 13 field group extractions)
   balances recovery granularity against write overhead. Losing one stage's
   extractions (5-15 minutes) on crash is acceptable; losing the entire
   pipeline (30-60 minutes) is not.

3. **Timestamped format** -- stale checkpoint detection prevents silently
   resuming from a days-old checkpoint where the codebase may have changed
   significantly. The 24-hour warning threshold is a heuristic, not a hard
   block.

4. **BEGIN IMMEDIATE locking** -- prevents race conditions if multiple worker
   instances accidentally run against the same database. The exclusive lock
   ensures only one writer at a time.

5. **Internal keys excluded from progress** -- the `_agent_context` checkpoint
   is an implementation detail. Users see "1/3 stages complete" not "2/4
   stages complete" when only the agent and context stages are done.

## Configuration

No user-facing configuration for checkpointing. It is always active when the
job store has a `_db_path` attribute (SQLite-backed). In-memory stores (used
in tests) skip checkpointing with a logged warning.

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/pipeline/checkpoint.py` | `CheckpointManager` class: save, load, clear operations |
| `fitz_forge/planning/pipeline/orchestrator.py` | Pipeline execution loop that calls checkpoint manager |
| `fitz_forge/background/worker.py` | Detects interrupted jobs, manages resume flag, graceful shutdown |
| `fitz_forge/models/jobs.py` | `JobState` enum including `INTERRUPTED` state |

## Related Features

- [Per-Field Extraction](per-field-extraction.md) -- the stage outputs that
  get checkpointed
- [LLM Providers](llm-providers.md) -- provider health checks run before
  resuming to verify the model is still available
