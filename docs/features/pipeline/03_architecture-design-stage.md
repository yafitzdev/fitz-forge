# Architecture-Design Stage

## Problem

Architectural planning requires exploring multiple approaches, evaluating tradeoffs,
making design decisions (ADRs), defining components, specifying data models, and
proposing concrete artifacts. This is the most complex stage in the pipeline, producing
the most structured output. Small local LLMs struggle with the volume: a single prompt
asking for approaches, components, ADRs, and artifacts in one JSON response routinely
fails. Additionally, the LLM's proposed architecture must be verified against the actual
codebase to catch hallucinated interfaces, impossible data flows, and missed patterns.

## Solution

A merged architecture + design stage with focused investigations, 6 post-reasoning
verification agents, and 6 small per-field-group extractions. The stage supports two
reasoning modes: combined (one call) for large context windows, and split (architecture
then design) for smaller models. Verification agents run in parallel where possible
to catch architectural flaws before extraction begins.

## How It Works

### Progress Range

0.25 to 0.65 of overall pipeline progress.

### Execute Flow

The `ArchitectureDesignStage.execute` method runs these steps:

1. **Focused investigations** -- `_investigate` pre-digests source code by running
   parallel LLM calls on specific aspects of the codebase (contracts, data flow,
   patterns). Results are formatted as `findings`.

2. **Reasoning** -- Either combined or split mode:
   - **Combined** (default): Single LLM call using `architecture_design.txt` prompt
     with tool access for on-demand file reading.
   - **Split** (`split_reasoning=True`): Two sequential calls. Architecture reasoning
     uses `architecture.txt`, then design reasoning uses `design.txt` with the
     architecture decision injected. Each call uses approximately 8K tokens instead of
     29K, enabling smaller context windows. Auto-enabled when `context_length < 32768`.

3. **Verification agents** (6 agents) -- Post-reasoning checks that run against the
   actual codebase:

   | Agent | Name             | Inputs          | Purpose                                    |
   |-------|------------------|-----------------|--------------------------------------------|
   | 1     | Contracts        | reasoning + krag | Extract actual interface contracts          |
   | 2     | Data Flow        | reasoning + krag | Trace data flow through proposed paths     |
   | 3     | Sketch           | 1 + 2           | Write pseudocode against real interfaces    |
   | 4     | Patterns         | reasoning + krag | Find existing patterns similar to proposal |
   | 5     | Assumptions      | 1+2+3+4         | Surface and verify every assumption        |
   | 6     | Type Boundaries  | reasoning + krag | Audit runtime types, catch data loss       |

   Agents 1, 2, 4, 6 run in parallel. Agent 3 depends on 1+2. Agent 5 depends on all.
   Each agent gets `max_tokens=4096` and `temperature=0`. Failures are non-fatal.

4. **Self-critique** -- The combined reasoning + verification findings are critiqued
   against the codebase context.

5. **Per-field-group extraction** -- 6 independent extraction calls:

   | Group          | Fields                                                    |
   |----------------|-----------------------------------------------------------|
   | `approaches`   | `approaches`, `recommended`, `reasoning`, `scope_statement` |
   | `tradeoffs`    | `key_tradeoffs`, `technology_considerations`              |
   | `adrs`         | `adrs` (title, context, decision, rationale, etc.)        |
   | `components`   | `components`, `data_model`                                |
   | `integrations` | `integration_points`                                      |
   | `artifacts`    | `artifacts` (filename, content, purpose)                  |

   Groups `approaches`, `adrs`, `artifacts`, `components`, and `integrations` receive
   the synthesized codebase context for grounding.

6. **Post-extraction validators**:
   - `ensure_min_adrs` -- Guarantees at least one ADR exists.
   - `ensure_valid_artifacts` -- Validates artifact filenames and content.
   - `ensure_correct_artifacts` -- Checks artifacts reference real codebase patterns.

7. **Pydantic validation** -- Split into `ArchitectureOutput` and `DesignOutput`.

### Adaptive Context Delivery

The reasoning prompt receives codebase context sized to fit the model's context window:

- **Full signal**: `findings + raw_summaries` when combined length is under 200K
  characters (approximately 50K tokens).
- **Degraded signal**: `findings + gathered_context` (synthesized overview only) for
  large codebases exceeding the budget.

The budget constant is `_REASONING_KRAG_BUDGET_CHARS = 200_000`.

### Fuzzy Matching for Recommended Approach

`parse_output` uses `difflib.get_close_matches` to correct the `recommended` field
when the LLM returns a name that does not exactly match any approach name. Cutoff is
0.4 (permissive). If no fuzzy match is found, the first approach is used as fallback.

### Artifact Duplicate Warnings

If the orchestrator's `_check_artifact_duplicates` found existing files matching
proposed artifacts, these warnings are injected into the `binding_constraints` section
of the prompt so the LLM sees them during reasoning.

## Key Design Decisions

1. **6 verification agents over a single critique.** Each agent has a focused task
   (contracts, data flow, types) rather than a vague "check everything" mandate.
   Parallel execution keeps latency manageable.
2. **Split mode for small context windows.** Two 8K-token calls are more reliable than
   one 29K-token call on models with 32K context. The architecture decision is injected
   into the design prompt to maintain coherence.
3. **Adaptive context budget.** The 200K character budget prevents context overflow on
   large codebases. Degraded signal (synthesized only) is still enough for extraction
   grounding.
4. **Fuzzy matching over strict equality.** LLMs routinely rephrase approach names
   between the reasoning and extraction passes. Fuzzy matching with a 0.4 cutoff
   catches "Direct Integration" matching "Direct API Integration" without accepting
   completely wrong matches.
5. **Pure Python duplicate detection.** The artifact duplicate checker uses keyword
   matching against the structural index -- no LLM call. Fast, deterministic, and
   avoids hallucination.

## Configuration

| Setting            | Default  | Description                                       |
|--------------------|----------|---------------------------------------------------|
| `split_reasoning`  | `False`  | Use two sequential reasoning calls instead of one |
| `context_length`   | varies   | Auto-enables split mode when below 32768          |

## Files

| File                                                        | Role                              |
|-------------------------------------------------------------|-----------------------------------|
| `fitz_forge/planning/pipeline/stages/architecture_design.py`| Stage implementation              |
| `fitz_forge/planning/schemas/architecture.py`               | `ArchitectureOutput` model        |
| `fitz_forge/planning/schemas/design.py`                     | `DesignOutput` model              |
| `fitz_forge/planning/prompts/architecture_design.txt`       | Combined reasoning prompt         |
| `fitz_forge/planning/prompts/architecture.txt`              | Split architecture prompt         |
| `fitz_forge/planning/prompts/design.txt`                    | Split design prompt               |
| `fitz_forge/planning/prompts/verify_contracts.txt`          | Agent 1 prompt                    |
| `fitz_forge/planning/prompts/verify_data_flow.txt`          | Agent 2 prompt                    |
| `fitz_forge/planning/prompts/verify_sketch.txt`             | Agent 3 prompt                    |
| `fitz_forge/planning/prompts/verify_patterns.txt`           | Agent 4 prompt                    |
| `fitz_forge/planning/prompts/verify_assumptions.txt`        | Agent 5 prompt                    |
| `fitz_forge/planning/prompts/verify_type_boundaries.txt`    | Agent 6 prompt                    |
| `fitz_forge/planning/pipeline/validators.py`                | Post-extraction validators        |

## Related Features

- **Context Stage** -- Provides requirements, constraints, scope, and existing files
  as binding constraints for this stage's prompt.
- **Agent Context Gathering** -- Provides `raw_summaries` for reasoning and
  `_gathered_context` for extraction grounding.
- **Coherence Check** -- Verifies that architecture output is consistent with
  context requirements and downstream roadmap phases.
- **Confidence Scoring** -- Scores architecture and design sections independently
  using section-specific criteria.
