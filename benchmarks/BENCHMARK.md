# Benchmarking Guide

Benchmarks evaluate plan quality by running the planning pipeline against a fixed codebase and scoring the output with Sonnet-as-Judge.

## Quick Start

```bash
# 1. Load model in LM Studio (--parallel 2 for concurrent runs)
lms load qwen3-coder-next-reap-40b-a3b-i1 -y -c 65536 --parallel 2

# 2. Run 5 plans, 2 at a time
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
  --runs 5 -p 2 \
  --source-dir ../fitz-ai \
  --context-file benchmarks/ideal_context.json \
  --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
  --score

# 3. Results land in benchmarks/results/decomposed_YYYYMMDD_HHMMSS/
#    - plan_NN.json     per-run plan output
#    - run_NN.json      per-run metadata (timing, decisions, success)
#    - SUMMARY.md       aggregate stats
#    - score_prompt_NN.md  evaluation prompts (when --score is used)
```

## Commands

### `decomposed` (primary)

Runs the decomposed pipeline (decision decomposition + resolution + synthesis).

```
Options:
  --runs N           Number of plans to generate (default: 3)
  --source-dir PATH  Target codebase (e.g. ../fitz-ai)
  --context-file F   JSON with pre-gathered retrieval context
  --query TEXT       Task description
  --score            Generate scoring prompts after runs
  -p, --parallel-runs N  Run N plans concurrently (default: 1)
```

### `reasoning`

Runs the monolithic 3-stage pipeline (context + arch+design + roadmap+risk). Older, kept for comparison.

### `retrieval`

Benchmarks code retrieval only (no LLM planning). Tests which files the agent finds for a given query.

### `prepare-scoring`

Generates scoring prompts from existing plan files without re-running the pipeline.

## Parallel Runs

LM Studio supports concurrent request batching. With `--parallel N` on model load, multiple requests are served simultaneously via continuous batching.

### Setup

The LM Studio `--parallel` flag and the benchmark `-p` flag must match:

```bash
# Load with 2 parallel slots
lms load qwen3-coder-next-reap-40b-a3b-i1 -y -c 65536 --parallel 2

# Run with 2 concurrent plans
.venv/Scripts/python -m benchmarks.plan_factory decomposed --runs 5 -p 2 ...
```

Runs are batched: with `-p 2` and `--runs 5`, execution is 3 batches (2+2+1).

### Throughput Scaling (RTX 5090 32GB, 40B Q5_K_S MoE, 65K ctx)

Measured with `benchmarks/test_parallel_throughput.py`:

| Concurrency | Combined Throughput | Gain vs N=1 | Per-request tok/s | TTFT |
|:-----------:|:-------------------:|:-----------:|:-----------------:|:----:|
| 1 | 150.6 tok/s | 1.00x | 158.9 | 0.76s |
| 2 | 209.1 tok/s | **1.39x** | 114.0 | 1.80s |
| 3 | 234.2 tok/s | 1.56x | 83.6 | 1.90s |
| 4 | 249.0 tok/s | 1.65x | 67.1 | 2.59s |

**N=2 is the sweet spot.** +39% throughput with only 28% per-request slowdown. N=3+ has diminishing returns because the model is memory-bandwidth bound — all requests compete for the same VRAM bandwidth to read model weights.

### Wall Time Impact

For a typical 5-run benchmark (~300s per run):

| Mode | Batches | Est. Wall Time | Savings |
|------|---------|---------------|---------|
| `-p 1` (sequential) | 5 | ~1500s | baseline |
| `-p 2` | 3 (2+2+1) | ~1050s | ~30% |

## Scoring

Plans are evaluated on 6 dimensions (each 1-10, total /60):

| Dimension | What it measures |
|-----------|-----------------|
| **file_identification** | Did the plan find the right files to modify? |
| **contract_preservation** | Does the plan preserve existing method signatures and behavior? |
| **internal_consistency** | Do the decisions and artifacts agree with each other? |
| **codebase_alignment** | Are method names, field names, imports correct (not fabricated)? |
| **implementability** | Could a developer follow this plan and produce working code? |
| **scope_calibration** | Is the scope appropriate — not too narrow, not too broad? |

### Scoring workflow

**Sequential assessment protocol (required for pipeline experiments):**

Run plans one at a time in this sequence so you can catch regressions early:

```
1 plan → assess → 1 plan → assess → 1 plan → assess → 2 plans → assess both → synthesize
```

Commands:

```bash
# Plans 1, 2, 3 (run one at a time, score and assess after each)
.venv/Scripts/python -m benchmarks.plan_factory decomposed --runs 1 \
  --source-dir ../fitz-ai --context-file benchmarks/ideal_context.json \
  --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
  --score

# Plans 4-5 (run together)
.venv/Scripts/python -m benchmarks.plan_factory decomposed --runs 2 \
  --source-dir ../fitz-ai --context-file benchmarks/ideal_context.json \
  --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
  --score
```

After each run, score with parallel Sonnet subagents (one per plan):

```
Read score_prompt_NN.md and follow ALL instructions to score the plan. Output ONLY the final JSON scorecard.
```

Extract scores from JSON: `file + contract + consistency + alignment + implementability + scope`.

After all 5 plans, synthesize: compare to baseline avg, note floor/ceiling shifts, update tracker.

**When to abort early:**
- If plans 1-2 both show obvious regressions (score < 30 or same failure mode as what you're trying to fix), stop immediately and diagnose before running more.
- If a plan reveals a bug in the pipeline code (wrong output, crash, incorrect behavior), stop the benchmark sequence, fix the bug, then restart from plan 1.
- Do NOT continue generating plans if a bug is confirmed — additional plans waste time and produce misleading data.

**Full 5-run batch (verification only, not for active experiments):**

```bash
.venv/Scripts/python -m benchmarks.plan_factory decomposed --runs 5 -p 2 \
  --source-dir ../fitz-ai --context-file benchmarks/ideal_context.json \
  --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
  --score
```

1. `--score` flag writes `score_prompt_NN.md` files alongside plans
2. Feed these to Claude Code subagents for Sonnet-as-Judge evaluation
3. No Anthropic SDK needed — evaluation runs through Claude Code's own interface

## Architecture

```
plan_factory.py          CLI entry (typer)
  |
  +-- _run_decomposed_once()    creates fresh config/client/pipeline per run
  |     |
  |     +-- AgentContextGatherer    code retrieval (skipped via _bench_override_files)
  |     +-- DecomposedPipeline      decision decompose -> resolve -> synthesize
  |           |
  |           +-- LM Studio API     http://localhost:1234/v1 (OpenAI-compat)
  |
  +-- asyncio.gather()          batches N runs when -p N > 1
  |
  +-- eval_plans.py             scoring prompt generation (--score)
```

Each concurrent run gets its own `LMStudioClient` instance. No shared mutable state between runs — safe for parallel execution.

### Key files

| File | Purpose |
|------|---------|
| `benchmarks/plan_factory.py` | Benchmark runner CLI |
| `benchmarks/eval_plans.py` | Scoring prompt generation |
| `benchmarks/eval_prompt.py` | Prompt template for Sonnet-as-Judge |
| `benchmarks/ideal_context.json` | Pre-gathered retrieval context (fixed across runs) |
| `benchmarks/test_parallel_throughput.py` | Standalone parallel throughput test |
| `benchmarks/results/streaming-task-tracker.md` | Run log with all results and session handoffs |

## Fixed Context

Benchmarks use `ideal_context.json` to bypass code retrieval and test only the planning pipeline. This file contains a `file_list` of pre-selected source files from the target codebase. The agent still runs its post-processing (structural overview, seed splitting, tool pool) but skips the LLM-based retrieval step.

## Important Notes

- The `--query` matters. The default query ("Add token usage tracking") is a different task than the streaming benchmark. Scores are NOT comparable across different queries.
- Temperature is non-zero (0.7 for generation) — expect variance between runs. Always run 5+ plans and report mean + range.
- Each run creates a fresh pipeline instance. No state leaks between runs.
- Model must be loaded in LM Studio before running. The health check verifies connectivity but won't auto-load.
