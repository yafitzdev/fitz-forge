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

1. **Score** — compute BOTH tiers using the task's taxonomy:
   - **Tier-1 (deterministic)** — automatic, parses artifacts, checks
     fabrication / yield / consistency. Coarse — won't catch semantic
     wiring bugs (e.g. streaming method calling its blocking sibling).
   - **Tier-2 (Sonnet taxonomy)** — automatic via `claude -p` per plan
     (`--score-v2` triggers both tiers; pass `--no-tier2` to skip).
     Returns an architecture id (A1-A5) + per-file ids (E1-E6, R1-R5,
     S1-S3) and aggregates to a `taxonomy_average`.
   - **A bug only counts as fixed when both tiers reflect the
     improvement.** Tier-1 alone can pass while Tier-2 reveals the plan
     is broken end-to-end.
2. **Triage** — read all failure patterns from BOTH tier reports
   (`SCORE_V2_SUMMARY.md` for Tier-1 distribution, `SCORE_V2_TAXONOMY.md`
   for the Sonnet classifications + qualitative notes). Add each pattern
   to `bug_register/` with an impact score (1–10).
   - 10 = most plans fail wholesale (e.g. dominant Tier-2 architecture
     classification at the bottom of the taxonomy across ≥80% of plans)
   - 5 = blocks one file per plan (e.g. one per-file class consistently
     drops to the worst tier)
   - 1 = cosmetic
   When a failure shows up in Tier-2 but not Tier-1, that is itself a
   meta-bug — the deterministic check is missing the invariant. The fix
   is usually a new check in `fitz_forge/planning/artifact/closure.py`
   or `validate.py`, not a prompt change.
3. **Pick** — select the single highest-impact open bug.
4. **Fix** — implement generalized to every variant of the failure
   shape. Ask: *"does this apply to any codebase/language, or is it
   specific to this task?"* If specific, don't ship it.
5. **Replay-validate** — replay a snapshot (~5 min). Re-runs synthesis
   only with the new code. **Inspect both tiers in the replay output.**
   Promote to full benchmark only if both improve (or one improves and
   the other holds).
6. **Regression-check** — re-score ALL previous task benchmarks with
   the new code. Both tiers. If any regress, revert.
7. **Mark done** — update the bug file (status → resolved, add closed
   date and fix description), loop to step 1.

## Exit criteria

- **Both tiers** satisfy: 10 runs with 90+ average on Tier-1 deterministic
  AND ≥70 average on Tier-2 taxonomy, at most 1 dud below those bars on
  each tier.
- OR: all open bugs have impact ≤ 3 and no fix is available without
  regressing other tasks.

Historically the loop has been run on Tier-1 alone — the track record
below records Tier-1 numbers. Tier-2 baselines will be filled in as
tasks get re-scored. Expect the initial Tier-2 numbers to be well below
Tier-1 until the B9-family invariants are enforced.

## Commands

```bash
# Baseline / fresh run — runs BOTH tiers automatically
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
    --runs 10 \
    --source-dir <codebase> \
    --context-file benchmarks/challenges/<task>/ideal_context.json \
    --query "$(cat benchmarks/challenges/<task>/user_prompt.txt)" \
    --taxonomy benchmarks/challenges/<task>/taxonomy.json \
    --score-v2

# Replay (fast validation, ~5 min) — also runs both tiers
.venv/Scripts/python -m benchmarks.plan_factory replay \
    --snapshot benchmarks/results/<run>/traces_01/snapshot_after_decision_resolution.json \
    --source-dir <codebase> \
    --context-file benchmarks/challenges/<task>/ideal_context.json \
    --query "$(cat benchmarks/challenges/<task>/user_prompt.txt)" \
    --score-v2

# Retroactive Tier-2 on an existing run (when plans already exist but
# SCORE_V2_TAXONOMY.md doesn't — e.g. a run from before Tier-2 was
# automated).
.venv/Scripts/python -m benchmarks.plan_factory score-taxonomy \
    benchmarks/challenges/<task>/results/<run_dir> \
    --taxonomy benchmarks/challenges/<task>/taxonomy.json

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
