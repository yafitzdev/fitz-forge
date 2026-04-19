# Plan Scorer V2 — Reference

How a benchmark-produced plan gets scored. This is the **evaluation harness**
we use to measure the planning pipeline itself — it never runs in production
and the plans users receive never carry a score. It's an internal tool for
catching regressions, comparing model/config changes, and validating that
pipeline improvements actually improve plan quality.

## What gets scored

A plan JSON produced by `fitz-forge` (either via the CLI, the MCP server, or
a benchmark run). The scorer reads:

- `design.artifacts` — the generated code, one dict per file with `filename`
  and `content`.
- `roadmap.phases` — the ordered work plan with verification commands.
- `context.needed_artifacts`, `decision_decomposition.decisions` — used by
  the completeness check to know what the task required.

Given those, the scorer produces a report with six numbers and a qualitative
taxonomy classification.

## Inputs the scorer needs

To score a plan, the scorer needs three things besides the plan itself:

| Input | Purpose | Where |
|---|---|---|
| Structural index of the target codebase | Validating that symbols in the plan resolve to real classes / methods / functions | `ideal_context.json` → `synthesized` field, or `source_dir` for a full rescan |
| Task taxonomy | Defines the quality tiers for the overall architecture + each critical file | `benchmarks/challenges/<task>/taxonomy.json` |
| Task query | The original user prompt | Recorded per run, replayed to the Sonnet grader so it can assess relevance |

## Two tiers

### Tier 1 — deterministic

No LLM calls. Same plan in, same score out. Produces one composite number
(`deterministic_score`, 0–100) and four dimension scores (each 0–100) that
measure orthogonal qualities of the plan.

#### The four dimensions

**Coverage** — what fraction of the files the task *requires* actually
shipped with real implementations. Strict: a file that ships as `raise
NotImplementedError` counts as uncovered, even though it technically exists.
Computed against the `required_files` list in the task's taxonomy.

**Craft** — on the files that did ship, how good is the code? The mean of
artifact-quality (parseability, fabrication checks, correct return types,
correct behaviour like `yield` for streaming artifacts, no
`NotImplementedError`, no `sys.stdout`) and cross-file consistency
(method-name agreement, type agreement, no duplicate content), both
normalized to 0–100.

**Groundedness** — do the references in the plan resolve against the real
codebase? Runs the same full-codebase grounding check the production
pipeline uses (`fitz_forge.planning.validation.grounding.check.
check_all_artifacts`) and reports the fraction of artifacts free of
violations. Catches chained fabrications that per-artifact quality checks
miss — a route calling `service.query_stream()` where a sibling artifact
invents a matching stub so both look self-consistent.

**Actionability** — can an agent actually execute this plan end-to-end?
Measures the fraction of roadmap phases that carry a concrete
`verification_command` (non-empty, non-placeholder). Plans with no roadmap
score 0.

These four dimensions don't mechanically combine into `deterministic_score`
— they're a more interpretable split of *similar* underlying checks and
show up alongside the composite in `SCORE_V2_SUMMARY.md`. The composite
formula is kept for backward compatibility with older runs.

#### The composite formula

```
completeness     = required_files_present / required_files_total   (0-1)
artifact_quality = size-weighted mean of per-artifact scores         (0-100)
consistency      = consistency_checks_passed / total                 (0-1)

deterministic_score = completeness * 30
                    + artifact_quality * 0.5
                    + consistency * 20
```

Max = 100.

#### Per-artifact checks

Each artifact runs a battery of deterministic checks:

| Check | Method | Output |
|---|---|---|
| Parseable | `ast.parse(content)` with import-split + class-wrap fallbacks | bool |
| No fabricated `self.method()` | Resolve against target class's real method list on disk | violation count |
| No fabricated `self._xxx.method()` | Resolve `_xxx` type from `__init__` attrs, check method exists | violation count |
| No fabricated `request.field` | Typed-attribute validation against known request models | violation count |
| No fabricated classes | `ClassName(...)` constructors validated against full codebase index | violation count |
| Has yield (streaming artifacts) | `yield` statement present in AST | bool |
| Has correct return type | Function annotation matches streaming / generator expectations | bool |
| No `NotImplementedError` | String check | bool |
| No `sys.stdout` | String check | bool |

Per-artifact score = `(checks_passed / total_checks) * 100`. Weights by line
count so a 222-line engine.py drives the artifact-quality average more than
a 25-line stub.

#### Cross-artifact consistency

| Check | What it catches |
|---|---|
| Method-name agreement | Artifact A calls `service.query_stream()`, artifact B defining the service must expose `query_stream()` — not `answer_stream()`. |
| Type agreement | Engine returns `Iterator[str]` → route must consume `Iterator[str]`, not `AsyncGenerator`. |
| No duplicate content | Same filename can't appear twice with identical content. |
| Parallel-method signatures | `generate_stream()` must share parameters with `generate()` it parallels. |

### Tier 2 — taxonomy classification

One Sonnet call per plan, invoked headless via `claude -p`. Sonnet receives:

1. The plan JSON.
2. The Tier 1 deterministic report.
3. The task's taxonomy tables (architecture + per-file).
4. The structural index of the target codebase.
5. The original task query.

Sonnet's job is **to classify, not to score**. It picks:

- Which architecture taxonomy entry (A1, A2, …) the plan's recommendation
  matches.
- Which per-file taxonomy entry (E1–E6 for engine, R1–R5 for routes, …) each
  critical file's implementation matches.

The classifications map to pre-defined scores via the taxonomy's own
`score` field. Sonnet cannot override deterministic findings — if Tier 1
flagged 3 fabricated methods in engine.py, Sonnet cannot classify it above
a "has fabrications" tier.

#### Taxonomy score

```
architecture_score = entry score for the plan's classified architecture
per_file_mean      = mean of per-file classified scores
taxonomy_score     = architecture_score * 0.4 + per_file_mean * 0.6
```

## Combined final score

```
final_score = deterministic_score * 0.6 + taxonomy_score * 0.4
```

60/40 in favour of deterministic because deterministic has zero variance
and the taxonomy grader has some noise (Sonnet calls on the same plan on
different days can land ±2–3 points). Final score is what you compare
across runs to say "benchmark X is better than benchmark Y."

## Taxonomies live per task

Taxonomies are authored per challenge as `benchmarks/challenges/<task>/
taxonomy.json` — the scorer loads them via `load_taxonomy`. Schema:

```json
{
  "task_name": "streaming_implementation",
  "task_description": "Add query result streaming …",
  "required_files": ["engine.py", "routes/query.py"],
  "architecture_taxonomy": {
    "entries": [
      {"id": "A1", "pattern": "…", "quality": "BEST", "score": 100, "description": "…"},
      …
    ]
  },
  "file_taxonomies": {
    "engine.py": {"entries": [{"id": "E1", "pattern": "…", "score": 100, "description": "…"}, …]},
    "routes/query.py": {"entries": […]},
    …
  }
}
```

The framework is codebase-agnostic; the taxonomy captures what "good"
means for a specific task. Authoring a new taxonomy for a new benchmark:
run 5 plans against the task, manually classify them into 4–6 quality
tiers, write the JSON.

## Running the scorer

End-to-end on a fresh 5-plan benchmark (T1 + T2, bundled):

```bash
python -m benchmarks.plan_factory decomposed \
  --runs 5 --source-dir ../your-codebase \
  --context-file benchmarks/challenges/<task>/ideal_context.json \
  --query "$(cat benchmarks/challenges/<task>/user_prompt.txt)" \
  --taxonomy benchmarks/challenges/<task>/taxonomy.json \
  --score-v2
```

Re-scoring an existing run directory without regenerating plans:

```bash
python -m benchmarks.plan_factory prepare-scoring-v2 \
  --results-dir benchmarks/challenges/<task>/results/<timestamp>_run_NNN \
  --context-file benchmarks/challenges/<task>/ideal_context.json \
  --source-dir ../your-codebase \
  --taxonomy benchmarks/challenges/<task>/taxonomy.json
```

Skip Tier 2 (deterministic only, no Sonnet calls — useful for quick
iteration):

```bash
… --no-tier2
```

## Outputs

| File | Content |
|---|---|
| `scores_v2.json` | Full structured results: per-plan deterministic report (all four dimensions + composite), taxonomy classifications, final scores, batch averages |
| `SCORE_V2_SUMMARY.md` | Human-readable Tier 1 summary: per-plan table with Coverage / Craft / Groundedness / Actionability / Final, batch averages and ranges |
| `scores_v2_taxonomy.json` | Tier 2 structured results: per-plan taxonomy IDs + architecture distribution + file-id distribution |
| `SCORE_V2_TAXONOMY.md` | Human-readable Tier 2 summary: distributions + per-plan taxonomy classification table |
| `score_v2_prompt_NN.md` | The exact prompt sent to Sonnet for plan NN (reproduces Tier 2 offline) |

## Design principles

- **Zero variance on Tier 1.** A plan scored today and a year from now gives
  the same numbers if the scorer code is unchanged. No LLM calls in the
  deterministic path.
- **Sonnet classifies, it doesn't score.** The scoring formula is fixed
  once a taxonomy is authored. Sonnet picks entries from a rubric;
  scores come from the rubric's `score` fields. Eliminates scorer drift.
- **Four named dimensions, not one opaque composite.** Coverage / Craft /
  Groundedness / Actionability each catch a specific failure mode. The
  composite `deterministic_score` is kept for backward compatibility but
  the dimensions are what's reportable.
- **Deterministic findings override LLM judgement.** Sonnet can flag
  additional issues (semantic errors, wrong algorithms) but cannot
  contradict Tier 1.
- **Taxonomies are task-specific, framework is task-agnostic.** A new
  benchmark needs a new `taxonomy.json`. The scorer code doesn't change.

## Limitations

- **Taxonomy authoring is manual.** Creating `taxonomy.json` for a new task
  requires running ~5 plans and hand-classifying them. There's no
  automated taxonomy generator yet.
- **Completeness requires a taxonomy to declare `required_files`.**
  Without one, completeness falls back to "files referenced in ≥3 resolved
  decisions," which is a weaker signal.
- **Per-artifact checks are partly language-specific.** Python artifacts
  get the full battery (AST parse, typed attr validation, fabrication
  checks). Non-Python artifacts get the cross-language
  keyword-presence check and not much else. Adding a new language means
  porting the AST checks.
- **Sonnet classification has residual variance.** Same plan on different
  days scores within ~2–3 points. We mitigate by reporting 5-plan means,
  not single runs.

## Related docs

- [FIXER_LOOP.md](../benchmarks/FIXER_LOOP.md) — the benchmark-improvement
  methodology that consumes scorer output.
- [Features guide](features/) — each pipeline stage and infrastructure
  component that the scorer evaluates the output of.
- [Senior-engineer reviews](features/infrastructure/senior-engineer-reviews.md)
  — the production-time review layer the scorer was built to measure the
  impact of.
