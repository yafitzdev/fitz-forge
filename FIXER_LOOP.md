# The Fixer Loop

Autonomous benchmark-improvement cycle. Use when plan quality needs to
go up on a specific task. Every fix must be **codebase and programming
language agnostic** — no task-specific hacks.

## Folder layout

Every benchmark task lives in its own folder under
`benchmarks/challenges/<task_name>/` with a fixed structure:

```
benchmarks/challenges/<task_name>/
├── user_prompt.txt          # the original user ask, verbatim
├── taxonomy.json            # architecture tiers + per-file quality tiers
├── ideal_context.json       # file_list of ~30 relevant files + synthesized overview
└── bug_register/
    ├── B1-<slug>.md         # one file per bug
    ├── B2-<slug>.md
    └── ...
```

Each bug file follows the same header:

```markdown
# <ID> — <title>

**Status:** open | resolved
**Impact:** N/10
**Closed:** YYYY-MM-DD (if resolved)

**Evidence:** ...
**Generalization:** ...
**Fix:** ...
```

## Setup (new task)

1. Pick a target codebase + user prompt.
2. `mkdir benchmarks/challenges/<task_name>/bug_register`.
3. Write `user_prompt.txt` — the verbatim user ask.
4. Write `taxonomy.json` — architecture tiers (BEST → FAIL) and per-file
   quality tiers. Typically built with a Sonnet sub-agent that reads the
   codebase and returns ranked implementation options.
5. Write `ideal_context.json` — `file_list` of ~30 relevant files.
6. Run a baseline benchmark (see commands below).

## Loop (each cycle)

1. **Score** — compute deterministic scores using the task's taxonomy.
2. **Triage** — read all failure patterns from the run. Add to
   `bug_register/` with an impact score (1–10).
   - 10 = most plans fail wholesale
   - 5 = blocks one file per plan
   - 1 = cosmetic
3. **Pick** — select the single highest-impact open bug.
4. **Fix** — implement generalized to every variant of the failure
   shape. Ask: *"does this apply to any codebase/language, or is it
   specific to this task?"* If specific, don't ship it.
5. **Replay-validate** — replay a snapshot (~5 min). Only promote to
   full benchmark if replay shows improvement.
6. **Regression-check** — re-score ALL previous task benchmarks with
   the new code. If any regress, revert.
7. **Mark done** — update the bug file (status → resolved, add closed
   date and fix description), loop to step 1.

## Exit criteria

- 10 runs with 90+ average, at most 1 dud below 90.
- OR: all open bugs have impact ≤ 3 and no fix is available without
  regressing other tasks.

## Commands

```bash
# Baseline run
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
    --runs 10 \
    --source-dir <codebase> \
    --context-file benchmarks/challenges/<task>/ideal_context.json \
    --query "$(cat benchmarks/challenges/<task>/user_prompt.txt)" \
    --taxonomy benchmarks/challenges/<task>/taxonomy.json \
    --score-v2

# Replay (fast validation, ~5 min)
.venv/Scripts/python -m benchmarks.plan_factory replay \
    --snapshot benchmarks/results/<run>/traces_01/snapshot_after_decision_resolution.json \
    --source-dir <codebase> \
    --context-file benchmarks/challenges/<task>/ideal_context.json \
    --query "$(cat benchmarks/challenges/<task>/user_prompt.txt)" \
    --score-v2

# Manual scoring with the right taxonomy
.venv/Scripts/python -c "
import json
from pathlib import Path
from benchmarks.eval_v2_deterministic import run_deterministic_checks
from benchmarks.eval_v2_taxonomy import load_taxonomy

tax = load_taxonomy(Path('benchmarks/challenges/<task>/taxonomy.json'))
plan = json.loads(Path('benchmarks/results/<run>/plan_01.json').read_text())
r = run_deterministic_checks(
    plan,
    structural_index='',
    task_requires_streaming=False,
    taxonomy_files=tax.required_files,
    source_dir='<codebase>',
)
print(r.deterministic_score)
"
```

## Track record

| Task | Codebase | Language | Baseline | After loop | Runs |
|------|----------|----------|----------|------------|------|
| streaming_implementation | fitz-sage | Python | 68.85 | 97.70 | 30 |
| ranking_explanation | fitz-sage | Python | 68.85 | 97.08 | 10 |
| hoppscotch_sharing | hoppscotch | TypeScript | 71.86 | 79.50 | 10 |
