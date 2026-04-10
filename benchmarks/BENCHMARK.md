# Benchmarking Guide

Benchmarks evaluate plan quality by running the planning pipeline against a fixed codebase and scoring with the V2 deterministic scorer (0-100).

## Quick Start

```bash
# 1. Load model in LM Studio
lms load qwen3-coder-next-reap-40b-a3b-i1 -y -c 65536

# 2. Run plans with V2 scoring
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
  --runs 7 \
  --source-dir ../fitz-sage \
  --context-file benchmarks/ideal_context.json \
  --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
  --score-v2

# 3. Results land in benchmarks/results/YYYY-MM-DD_HH-MM-SS_run_NNN/
#    - plan_NN.json              per-run plan output
#    - run_NN.json               per-run metadata (timing, decisions, success)
#    - traces_NN/                per-run LLM call provenance + stage snapshots
#    - SUMMARY.md                aggregate stats (timing, architecture, stages)
#    - SCORE_V2_SUMMARY.md       deterministic scores per plan
#    - scores_v2.json            full scoring data (completeness, artifact quality, consistency)
#    - score_v2_prompt_NN.md     taxonomy prompts for Sonnet classification (Tier 2)
```

## Commands

### `decomposed` (primary)

Runs the decomposed pipeline (decision decomposition + resolution + synthesis).

```
Options:
  --runs N           Number of plans to generate (default: 3)
  --source-dir PATH  Target codebase (e.g. ../fitz-sage)
  --context-file F   JSON with pre-gathered retrieval context
  --query TEXT       Task description
  --score-v2         Run V2 deterministic scorer after generation
  --score            Generate V1 scoring prompts (legacy, Sonnet-as-Judge)
```

### `replay`

Replay a pipeline from a saved stage snapshot. Skips completed stages, re-runs only the remaining stages with the real LLM.

```bash
.venv/Scripts/python -m benchmarks.plan_factory replay \
  --snapshot benchmarks/results/.../traces_01/snapshot_after_decision_decomposition.json \
  --source-dir ../fitz-sage \
  --context-file benchmarks/ideal_context.json \
  --score-v2
```

Use this to test pipeline changes without re-running the full 10-minute pipeline. Available snapshots:
- `snapshot_after__pre_stages.json` — re-run everything from decomposition
- `snapshot_after_decision_decomposition.json` — re-run resolution + synthesis
- `snapshot_after_decision_resolution.json` — re-run only synthesis (artifact generation)

### `prepare-scoring-v2`

Rescore existing plans without re-running the pipeline.

```bash
.venv/Scripts/python -m benchmarks.plan_factory prepare-scoring-v2 \
  --results-dir benchmarks/results/YYYY-MM-DD_HH-MM-SS_run_NNN \
  --context-file benchmarks/ideal_context.json
```

### `reasoning` / `retrieval`

Legacy commands for monolithic pipeline and retrieval-only benchmarks.

## Scoring

### V2 Deterministic (Tier 1) — automatic, zero LLM cost

Plans scored on 3 dimensions (total /100):

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| **Completeness** | 0-30 | Required files present (from taxonomy) |
| **Artifact quality** | 0-50 | Size-weighted mean: parseable, fabrication, streaming behavior |
| **Consistency** | 0-20 | Cross-artifact method name + type agreement |

Same plan always gets the same score. Source-dir augmentation validates against the full codebase (not just retrieval subset).

### V2 Taxonomy (Tier 2) — Sonnet classification

Sonnet classifies each plan's architecture and artifacts into a predefined taxonomy:
- Architecture: A1 (best, full pipeline + streaming) to A5 (fail)
- Per-file: E1-E6 (engine), R1-R5 (routes), S1-S3 (synthesizer)

Run via Claude Code subagents on `score_v2_prompt_NN.md` files. Not yet automated.

### Scoring workflow

1. Run plans with `--score-v2` — deterministic scores are computed automatically
2. Check `SCORE_V2_SUMMARY.md` for per-plan breakdown
3. If investigating specific failures, check `scores_v2.json` for detailed artifact checks and consistency results
4. For architecture quality assessment, run Tier 2 taxonomy classification via Sonnet subagents

**When to abort early:**
- If plans 1-2 both show obvious regressions (score < 70 or same failure mode), stop and diagnose
- If a plan reveals a pipeline bug (crash, wrong output), stop, fix, restart from plan 1
- Do NOT continue generating plans if a bug is confirmed

## Provenance & Replay

Every benchmark run produces full LLM call provenance in `traces_NN/`:
- `NNN_label.json` — every generate() call with messages, output, timing, max_tokens
- `snapshot_after_{stage}.json` — full prior_outputs dict after each pipeline stage

Use `replay` to jump back to any stage and re-run from there. This enables:
- Testing artifact generation changes without re-running decomposition (~50s saved)
- Testing synthesis changes without re-running resolution (~70s saved)
- Rapid A/B testing of prompt changes on the same decisions

## Architecture

```
plan_factory.py                  CLI entry (typer)
  |
  +-- _run_decomposed_once()     creates fresh config/client/pipeline per run
  |     |
  |     +-- configure_tracing()  enables LLM call provenance
  |     +-- AgentContextGatherer code retrieval (skipped via _bench_override_files)
  |     +-- DecomposedPipeline   decision decompose -> resolve -> synthesize
  |           |
  |           +-- generate()     centralized LLM calls (budget cap, retry, tracing)
  |           +-- generate_artifact()  artifact black box (validate + retry)
  |
  +-- _prepare_scoring_v2()      deterministic scoring + taxonomy prompts
  +-- _run_replay_once()         replay from saved snapshot

### Key files

| File | Purpose |
|------|---------|
| `benchmarks/plan_factory.py` | Benchmark runner CLI (decomposed, replay, prepare-scoring-v2) |
| `benchmarks/eval_v2_deterministic.py` | V2 deterministic scorer (completeness, artifact quality, consistency) |
| `benchmarks/eval_v2_taxonomy.py` | V2 taxonomy classification (Sonnet prompt builder + parser) |
| `benchmarks/streaming_taxonomy.json` | Task-specific taxonomy (A1-A5, E1-E6, R1-R5, S1-S3) |
| `benchmarks/ideal_context.json` | Pre-gathered retrieval context (fixed across runs) |
| `fitz_forge/llm/generate.py` | Centralized LLM call with budget cap + tracing |
| `fitz_forge/planning/artifact/` | Artifact generation black box (strategies + validation) |
| `docs/v2-scoring/TRACKER.md` | Run history, scoring formula, failure patterns |
```

## Fixed Context

Benchmarks use `ideal_context.json` to bypass code retrieval and test only the planning pipeline. This file contains a `file_list` of pre-selected source files from the target codebase. The agent still runs its post-processing (structural overview, seed splitting, tool pool) but skips the LLM-based retrieval step.

## Important Notes

- The `--query` matters. Scores are NOT comparable across different queries.
- Temperature is non-zero (0.7 for synthesis reasoning) — expect variance. Run 5+ plans and report mean + range.
- Each run creates a fresh pipeline instance. No state leaks between runs.
- Model must be loaded in LM Studio before running. The health check verifies connectivity but won't auto-load.
- Results folder auto-increments: `YYYY-MM-DD_HH-MM-SS_run_NNN`.
- Current baseline: run 91, avg 87.3/100, range 73.3-100.0 (6 plans).
