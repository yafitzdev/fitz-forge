# Session Handoff — 2026-04-16 (experiments)

Started from `session-handoff-2026-04-16-02.md`. This session ran the
fixer loop across two more tasks after the overnight streaming work,
then tried to go cross-language. Not the release stuff — pure
experiments + what's next.

## What we did this session

### 1. Streaming task — sealed the deal

Inherited from overnight: streaming task at 98.35 avg across 30 runs
on fitz-sage (Python). 6 perfect 100s, 27/30 ≥95, 2 duds at ~89.5
from a single scorer false positive. Committed as part of `a858a60`.

No new work on streaming this session — it's the baseline we regression-
check against now.

### 2. Ranking explanations task — second Python benchmark

Goal: prove the fixer-loop fixes aren't overfit to streaming. Same
codebase, different feature.

- Task: "Add query result ranking explanations so users can see why
  each source was ranked in its position."
- Taxonomy: `benchmarks/ranking_explanations/taxonomy.json`
- Context: `benchmarks/ranking_explanations/ideal_context.json`
- Bug register: `docs/v2-scoring/ranking_explanations/BUG_REGISTER.md`
- Baseline run_022: **68.85 avg** (10 plans). Completeness was broken —
  `ranker.py` and `reranker.py` present in 0/10 plans despite being in
  the decision evidence.
- Fixes that landed (all codebase/language agnostic):
  - Evidence-source artifact injection (`_enforce_decision_coverage`
    criterion 2): files cited as evidence in resolved decisions get
    auto-injected at min_refs=1.
  - Import-split parse recovery (`inference.py:try_parse` step 4):
    handles hybrid `import X` at indent 0 + `def method(self,...)` at
    indent 4 outputs.
  - Container-type fix (`inference.py:extract_type_name`): `list[X]`
    returns `list` not `X`, stops `items.append()` being flagged as
    `Foo.append()`.
  - Scorer consistency fix (in `benchmarks/eval_v2_deterministic.py`,
    gitignored): stdlib/framework methods (append, extend, post, etc.)
    skipped in `method_name_agreement` check; `source_dir` threaded
    through so codebase-method skip list uses full-disk scan.
- Final run_023: **97.08 avg** (10 plans). All 10 ≥ 90, 1 perfect 100.
- Committed as `648ab3d`.

### 3. Hoppscotch task — first cross-language try

Goal: see whether the pipeline works on TypeScript. Cloned
`hoppscotch/hoppscotch` (repo at `../hoppscotch`), targeting
`packages/hoppscotch-backend` (NestJS, ~42K LOC, 207 TS files).

- Task: "Add collection sharing via public link so users can share a
  read-only view of their API collections."
- Taxonomy: `benchmarks/hoppscotch_sharing/taxonomy.json`
- Context: `benchmarks/hoppscotch_sharing/ideal_context.json`
- Bug register: `docs/v2-scoring/hoppscotch_sharing/BUG_REGISTER.md`

Taxonomy and ideal_context were built by spawning two Sonnet sub-agents
(one for API/schema layer, one for ranking/retrieval — wait, that was
the ranking task. For Hoppscotch: one to find a foreign codebase, one
to build the context). The agents produced a BEST/GOOD/POOR/BAD
architecture tier structure based on reading the real code.

**The runs:**

| Run | Avg | Notes |
|-----|-----|-------|
| 024 (baseline) | 71.86 | 2/10 had 0 artifacts. Artifacts were 1-line TS signatures that accidentally parsed as Python call expressions. |
| 025 (first fix attempt) | **44.71** | **REGRESSION.** B2-hopp prompt change told model to "write full implementation, not signatures." Model obeyed — output real multi-line TS method bodies, which then failed Python AST `_check_parseable` and got rejected. The 1-line signatures had been "working" by accident. |
| 026 (validate fix) | 50.26 | Skipped Python AST checks for non-`.py` files. Better, but then `_check_empty` blocked everything because TS uses `function`/`async`/`export`, not `def`/`class`. |
| 027 (empty-check fix) | **79.50** | Broadened `_check_empty` keywords to `function`/`async`/`export`/`const`/`model`/`interface`/`enum`/`struct`/`fn`/`pub`. 0 zero-artifact plans. Completeness 30/30 on 7/10. |

**Honest read on Hoppscotch 79.50:**
- Completeness (language-agnostic): strong, 30/30 on most plans.
- Consistency (language-agnostic): 20/20 on all 10 plans.
- Artifact quality capped at ~45/50: we can't actually validate TS code.
  Python AST rejects it, we skip the check, artifact just passes through
  unchecked. So "artifact quality" only measures text heuristics.
- **We unblocked non-Python planning. We did NOT build cross-language
  validation. Tree-sitter migration is what would actually do that.**

Committed as `e2a96f6`.

## Full fix stack landed this session

All codebase-agnostic, language-agnostic, committed on `main`:

1. **Evidence-source artifact injection** — `synthesis.py:_enforce_decision_coverage` criterion 2. When a file is cited as the source of evidence in a resolved decision, it gets injected at min_refs=1 (instead of the original cross-reference threshold of 2). Also runs BEFORE the template fallback, so empty `needed_artifacts` plans still produce real artifacts.
2. **Import-split parse recovery** — `inference.py:try_parse` 4th step. When the model outputs top-level imports at indent 0 + indented method bodies at indent 4, split the imports and class-wrap only the body.
3. **Container-type annotation** — `inference.py:extract_type_name` + `_CONTAINER_TYPES`. `list[X]`/`dict[K,V]`/`set[X]` etc. return the container name (skipped via `_SKIP_NAMES`), not the element type.
4. **Generalized class fabrication check** — `closure.py:_iter_annotation_class_names` + `_emit_annotation_types`. Existence check now fires on parameter/return/variable annotations, `raise`, `except`, `isinstance`, `cast`, instantiation — not just `ClassName(...)`.
5. **Protocol widening** — `closure.py:_owner_is_protocol` + `_method_exists_anywhere`. Methods called on Protocol-typed receivers accepted when the method exists on any class in the codebase.
6. **Enum standard attrs** — `closure.py:_ENUM_STANDARD_ATTRS` + `_is_enum_class`. `Enum`/`IntEnum`/`StrEnum`/`Flag` accept `.value`, `.name`, etc.
7. **TypeVar detection** — `closure.py:_find_module_typevars`. Skips `T = TypeVar("T")` bindings plus single-letter uppercase names.
8. **Target class self-methods in prompt** — `context.py:_extract_target_self_methods` + `strategy.py:_surgical_grounding_block`. Real method list of the target class is injected into surgical and new-code prompts with explicit "do NOT invent new helper names" rule.
9. **Exact-duplicate closure violation dedup** — `closure.py:_dedupe_exact`.
10. **Data-model class validation** — `validate.py:_is_data_class`. `_check_empty` accepts Pydantic `BaseModel` / `dataclass` / `Enum` / `TypedDict` / any class with annotated fields.
11. **Language-aware validation dispatch** — `validate.py:_is_python_file`. Python AST-based checks skip for `.ts`/`.js`/`.go`/`.rs`/`.java`/`.prisma` files. Structural validation still Python-only.
12. **Cross-language `_check_empty` keywords** — broadened from `def`/`class` to `function`/`async`/`export`/`const`/`let`/`var`/`model`/`interface`/`enum`/`struct`/`fn`/`pub`. Only validation check that currently works across languages.
13. **`_strip_fences` preserves indentation** — `strategy.py`. No longer calls `.strip()` on raw output.
14. **`_RAW_CODE_INSTRUCTION` language-agnostic** — `strategy.py`. "code (full implementation, not just signatures or stubs)" instead of "Python code".
15. **NewCodeStrategy prompt** — `strategy.py`. Requests "FULL method/function body", not just the signature.
16. **Grounding uses full-codebase index** — `grounding/check.py`, `grounding/llm.py`, `orchestrator.py`. `source_dir` threaded through so `augment_from_source_dir` runs.
17. **Grounding parser uses `try_parse`** — `grounding/check.py`. With class-wrap + import-split fallback.

## Where things stand right now

- **Streaming (fitz-sage, Python):** 30 runs, 98.35 avg. Sealed.
- **Ranking explanations (fitz-sage, Python):** 10 runs, 97.08 avg. Sealed.
- **Collection sharing (hoppscotch, TypeScript):** 10 runs, 79.50 avg.
  Limited by inability to actually validate TS code.

Test suite: 964 pass, 1 skipped.

Version in `pyproject.toml`: still `0.6.1`. Not bumped.

## Repo restructure (happened late in the session)

After the experiments we cleaned up the benchmark area into a proper
shape. Everything task-specific now lives under
`benchmarks/challenges/<task_name>/`:

```
benchmarks/
├── FIXER_LOOP.md                             # methodology doc (moved from repo root)
├── plan_factory.py                           # _results_dir derives challenge from --context-file path
├── eval_v2*.py                               # kept
├── eval_retrieval.py                         # KEPT but CURRENTLY BROKEN (see Pending)
├── eval_retrieval_run.py                     # KEPT but depends on eval_retrieval.py
└── challenges/
    ├── streaming_implementation/
    │   ├── user_prompt.txt
    │   ├── taxonomy.json
    │   ├── ideal_context.json
    │   ├── retrieval_ground_truth.json       # NEW: per-challenge
    │   ├── bug_register/                     # 12 bugs, one md file each
    │   └── results/                          # run dirs moved here (gitignored)
    ├── ranking_explanation/
    │   └── (same layout, 5 bugs, 2 runs)
    └── hoppscotch_sharing/
        └── (same layout, 5 bugs, 4 runs)
```

Also deleted this session:
- Legacy V1 scorer scripts: `eval_deterministic.py`, `eval_plans.py`,
  `eval_prompt.py`, `eval_schemas.py` (all 0 refs; superseded by eval_v2).
- Specialty dev tools: `haiku_subagent_bench.py`, `replay_artifact.py`.
- Ad-hoc test scripts: 14x `benchmarks/test_*.py` (one-shot verification
  scripts from F1…F25 fix cycles).
- Pre-session run dirs 001-018 (no clear challenge owner).
- Stale logs: `bench_seeds5*.log`.
- Old `docs/v2-scoring/BUG_REGISTER.md` (split into bug_register/ folders).
- Old `docs/v2-scoring/V2-F*.md` + `TRACKER.md` moved to
  `docs/archive/failure-patterns-v1/`.

Commits this reorg:
- `7528278` — challenges folder structure + bug_register/ split
- `e8323c4` — run dirs moved into per-challenge results/
- `39a6c0d` — legacy script prune + retrieval_ground_truth.json split

## ⚠️ Things broken / pending — FIX FIRST NEXT SESSION

### 1. `eval_retrieval.py` + `eval_retrieval_run.py` are broken

They reference `benchmarks/retrieval_ground_truth.json` which was deleted
when we split it into per-challenge files. They need an update:

- Walk `benchmarks/challenges/*/retrieval_ground_truth.json` at startup,
  load each as a single-entry record, use the challenge folder name as
  the entry's ID (replaces the old numeric `id`).
- Replace `--ids` / `--category` CLI filters with `--challenge <name>`
  (can repeat or comma-separate).
- Write results into `benchmarks/challenges/<name>/results/retrieval_eval_<ts>.json`
  to match plan_factory's layout.
- `eval_retrieval_run.py` updates its defaults from `CATEGORY`/`IDS` to
  `CHALLENGES = None` (all).

**Open call:** the old schema also had `relevant_files` (softer signal —
nice-to-have files). Our new per-challenge files only have
`critical_files`. Either add `relevant_files` back or drop that signal
from the scorer. User deferred to next session.

About 20-30 LOC across the two files. No new deps.

### 2. Retrieval ground truth coverage is tiny

Central file had 40 entries covering many potential tasks. Per-challenge
split kept only 3 (streaming, ranking, hoppscotch). The other 37 task
definitions are gone. If you want those back, they each need to become
their own challenge folder with the full set of files (user_prompt,
taxonomy, ideal_context, retrieval_ground_truth, bug_register). The
original list is in git history before `39a6c0d` if needed.

### 3. Release v0.6.2 paused

From earlier in the session. Status:
- `CHANGELOG.md` has 0.6.1 (retro) and 0.6.2 entries, user-approved
  trim (3 highlight items, product-only, honest about what TS
  support actually is — "stop rejecting non-Python files", not
  "cross-language validation").
- `pyproject.toml` still says 0.6.1. **Not bumped yet.**
- No git tag yet.
- `README.md` cleanup still pending (user said "readme is still absolute
  shit" earlier — user wants it cleaned as part of the release).

DO NOT bump the version or tag without explicit GO — user wants to
review the changelog first.

## What's next (in priority order)

### 1. Release v0.6.2 (in-flight, paused)

Changelog entries for 0.6.1 (retro) and 0.6.2 are in `CHANGELOG.md`.
User pushed back on the 0.6.2 highlights being too stuffed — currently
trimmed to 3 headline items. **Don't touch `pyproject.toml` or `README.md`
or tag anything without explicit sign-off.** User wants to review the
changelog first.

### 2. README cleanup

User said "your readme is still absolute shit" — he wants it cleaned up
as part of the release. Don't start until the changelog is signed off.

### 3. Tree-sitter migration

User's framing: "cant we change the python ast tree-sitter for a general
one? it needs to work for any coding language."

Three options discussed (pick one WITH user, don't just start):

- **Option A — Full migration.** Replace every `ast.parse` + `NodeVisitor`
  with tree-sitter. Unified code path for all languages. ~2-3 days, high
  regression risk on streaming (97.70) and ranking (97.08).
- **Option B — Tree-sitter only for non-.py files.** Keep existing Python
  AST stack, add parallel tree-sitter stack for TS/Go/etc. Duplicated
  logic per language family. ~1 day, no Python regression risk. My
  recommendation.
- **Option C — Node-type abstraction layer.** Wrapper over both Python
  `ast` AND tree-sitter, exposing generic `walk`/`node_type`/`node_children`
  etc. Port closure/validate/inference to use the wrapper. ~3-4 days,
  cleanest long-term, highest short-term risk.

Current state of `ast` usage across product code:
- `closure.py` — `_ReferenceCollector` NodeVisitor with 10+ visitors
- `validate.py` — `_check_fabrication`, `_check_yield`, `_check_return_type`
- `inference.py` — `extract_type_name`, `try_parse`, class field extraction
- `grounding/check.py` — `check_artifact`, `_check_parallel_signatures`
- `grounding/index.py` — `augment_from_source_dir`

Expected payoff once tree-sitter lands: Hoppscotch artifact quality
should jump from ~35/50 to ~45+/50, pushing the benchmark into the low
90s (matching the Python tasks).

### 4. Clean up open bug registers

Three live bug registers:
- `docs/v2-scoring/BUG_REGISTER.md` — streaming (all resolved except
  B8 at impact 3, type_agreement false positive on same-name methods).
- `docs/v2-scoring/ranking_explanations/BUG_REGISTER.md` — ranking
  (all resolved).
- `docs/v2-scoring/hoppscotch_sharing/BUG_REGISTER.md` — hoppscotch
  (open: artifact quality ceiling, which tree-sitter fixes).

## Don't-lose-this context

- **Model loaded**: gemma-4-26b-a4b-it@q6_k. Still loaded. Don't switch
  — every model swap destroys CUDA context permanently on Blackwell
  (WDDM bug, see memory).
- **Config lives in TWO places** (both must be updated on model changes):
  - `C:\Users\yanfi\AppData\Local\fitz-forge\fitz-forge\config.yaml`
  - `C:\Users\yanfi\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\Local\fitz-forge\fitz-forge\config.yaml`
- **`benchmarks/` is gitignored**. Scorer fixes to
  `benchmarks/eval_v2_deterministic.py` and `benchmarks/plan_factory.py`
  (stdlib method skip in consistency check, replay scoring output,
  `--taxonomy` flag) are uncommitted and CANNOT be committed — the whole
  dir is ignored. They live on disk only.
- **Foreign codebase clone**: `C:\Users\yanfi\PycharmProjects\hoppscotch`
  (shallow clone, depth 1).
- **Fixer loop methodology** is documented in `CLAUDE.md` — setup,
  protocol, exit criteria, commands, track record.
- **User's strong preferences**: ONE bug at a time. Generalize fixes to
  EVERY variant of the failure shape (rule 10). Regression-check prior
  task benchmarks after every fix. Replay-first validation (5 min) over
  full benchmark (90 min). Don't implement non-trivial things without
  signing off first (rule 7). Don't cram changelog highlights.

## Recent commits (for grep-ability)

```
39a6c0d chore: prune legacy scripts + per-challenge retrieval ground truth
e8323c4 chore: move runs into per-challenge results/ dirs
7528278 chore: reorganize benchmark challenges into self-contained folders
e8698db docs: update fixer loop track record with Hoppscotch TS results
e2a96f6 feat: cross-language artifact generation — Hoppscotch TS avg 79.50
e7c41c1 docs: add fixer loop methodology to CLAUDE.md
648ab3d feat: decision-driven artifact injection + parse recovery — ranking avg 97.08
a858a60 feat: generalized fabrication guards + closure fixes — 30-run avg 98.35
```

## End of handoff
