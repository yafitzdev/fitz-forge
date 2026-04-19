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

   **Anti-bandaid check before coding:** look at the last 3-4 resolved
   bugs in this task's register. Do they share an underlying shape
   (e.g. "tree-sitter pattern predicate was too narrow", "metadata
   extraction missed a real-world variant", "scanner had wrong child-
   count assumption")? If yes, the right fix is NOT a fourth patch —
   it's a refactor that removes the class of bug (single canonical
   AST pass producing a unified index, replace per-walker shape
   predicates with one tested abstraction). Surface this in the cycle
   summary and either do the refactor OR justify why patching once
   more is acceptable (e.g. "only 3 instances, refactor cost too high
   for current marginal value"). Never silently stack.
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

**2026-04-18 — pivot from closure invariants to semantic-review gate.**
The B9/B11/B17 closure-shape invariants have been removed and replaced
by an LLM-based semantic-review pass that reads reasoning + decisions +
artifacts and reports contradictions. The gate runs inside
`generate_artifact_set` after the closure pass (closure is kept as a
cheap deterministic pre-filter). Discrepancies route through the same
`generate_artifact` regeneration path with natural-language feedback.
See `fitz_forge/planning/artifact/semantic_review.py`.

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

| Task | Codebase | Language | Baseline (T1/T2) | After loop (T1/T2) | Runs |
|------|----------|----------|------------------|--------------------|------|
| streaming_implementation | fitz-sage | Python | 68.85 / 61.8 | 99.7 / 94.6 | 5 after calibrated build-new prompt (2026-04-18/19). 5/5 A1, 5/5 E1, 4/5 S1, 3/5 R1. Prior peaks: closure-era 100.0/77.3; pre-fix gate 96.3/96.9; over-broad-fix 96.0/76.1 (regressed, then recovered via calibration) |
| ranking_explanation | fitz-sage | Python | 68.85 / 56.5 | 97.6 / 82.6 | 5 after design-regen (TODO #03 landed 2026-04-19, run_049). Design review fires then *regenerates* affected field groups (components/data_model, adrs, integrations) when senior-engineer critique finds issues; kept whichever pass has fewer issues. Regen fired on 5/5 runs, improved 4/5 (6→5 issues), fell back cleanly on 1/5. **A1 4/5** (historical max 1/5), **RR1 4/5** (historical max 2/5) — first time the pre_rerank_score preservation lift predicted by the TODO materialised. K1 2/5. Stretch target was 85; plan_02 (72.0) and plan_03 (72.5) outliers pulled the mean below. Prior: 98.0/74.5 after rubric injection only |
| hoppscotch_sharing | hoppscotch | TypeScript | 71.86 / 47.5 | 97.3 / 80.0 | 5 after calibrated build-new prompt + decision_text_map threading + language-aware scorer (2026-04-18/19). All 5 plans A1 ("Dedicated CollectionShare Module"), 4/5 P1 schema. T1 was artificially capped at ~74 under the Python-only scorer (every .ts file flagged unparseable); language-aware scorer accurately reflects per-file quality. Aggressive-fix peak for T2 was 78.7/95.0; calibration gave back 15 T2 points on service-layer fidelity but preserved the A1 architectural win |

T2 numbers backfilled as benchmarks get re-scored under automated Tier-2.
Note: closure-layer invariants (B9 family, B10, B11) only fire on Python
artifacts. Languages other than Python need either a tree-sitter
shape port of `closure.py` or task-specific deterministic checks.
