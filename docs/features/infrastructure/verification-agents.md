# Verification Agents

## Problem

The main reasoning pass for the Architecture+Design stage produces a free-form
architectural plan. Small local models are prone to hallucinating integration
points, assuming APIs exist that do not, proposing data flows through
nonexistent layers, and making type assumptions that silently lose data. The
self-critique pass catches surface issues (scope inflation, formatting) but
cannot verify claims against the actual codebase -- it only sees the reasoning
text, not the source code.

## Solution

Six post-reasoning verification agents that each examine a specific dimension
of the proposed architecture against the gathered codebase context. They run
after the main reasoning pass and before self-critique, producing findings that
are injected back into the reasoning text. This gives the self-critique pass
and downstream extraction verified ground truth to work with.

## How It Works

### Agent Overview

The six agents are split into two execution batches based on data dependencies:

**Parallel batch** (independent inputs -- all receive reasoning + krag_context):

| # | Agent | Method | Purpose |
|---|-------|--------|---------|
| 1 | Contract extraction | `_verify_contracts()` | Extracts actual interface contracts for proposed integration points from the codebase |
| 2 | Data flow tracing | `_verify_data_flow()` | Traces actual data flow through proposed modification paths in the source code |
| 4 | Pattern matching | `_verify_patterns()` | Finds existing patterns in the codebase similar to proposed approaches |
| 6 | Type boundary audit | `_verify_type_boundaries()` | Traces concrete runtime types across boundaries, catches silent data loss |

**Sequential batch** (depends on parallel results):

| # | Agent | Method | Depends On | Purpose |
|---|-------|--------|------------|---------|
| 3 | Sketch test | `_verify_sketch()` | Agents 1+2 | Writes pseudocode against real interfaces, flags mismatches |
| 5 | Assumption surfacing | `_verify_assumptions()` | All parallel | Surfaces and verifies every assumption in the proposed architecture |

### Execution Flow

`_run_verification_agents()` orchestrates the full sequence:

1. **Check precondition** -- if no gathered codebase context is available,
   skip all agents and return empty string. Verification without codebase
   evidence would produce hallucinated findings.

2. **Parallel batch** -- agents 1, 2, 4, 6 run concurrently via
   `asyncio.gather(return_exceptions=True)`. Each receives the full reasoning
   text, gathered codebase context, and job description. Each uses
   `max_tokens=4096` for focused output.

3. **Handle parallel failures** -- if any agent in the parallel batch raises
   an exception, its result is logged as a warning and replaced with an empty
   string. Other agents' results are preserved.

4. **Sequential: Agent 3 (sketch test)** -- receives the reasoning plus the
   contract sheet (agent 1) and data flow map (agent 2). If either input is
   unavailable, it receives a placeholder string `"(contract extraction
   unavailable)"` or `"(data flow tracing unavailable)"`.

5. **Sequential: Agent 5 (assumptions)** -- receives the reasoning plus all
   parallel results and the sketch test result. Missing inputs get
   `"(unavailable)"` placeholders.

6. **Assemble findings** -- non-empty agent outputs are assembled into labeled
   sections with `---` delimiters:
   - `INTERFACE CONTRACTS (verified against source)`
   - `DATA FLOW MAP (traced through source)`
   - `TYPE BOUNDARY AUDIT`
   - `EXISTING PATTERNS`
   - `FEASIBILITY REPORT`
   - `ASSUMPTION REGISTER`

7. **Inject into reasoning** -- the assembled findings are appended to the
   reasoning text under a `--- POST-REASONING VERIFICATION ---` header. This
   combined text then flows into self-critique and per-field extraction.

### Prompt Templates

Each agent has a dedicated prompt template in `fitz_forge/planning/prompts/`:

| Agent | Template File | Key Inputs |
|-------|---------------|------------|
| 1 | `verify_contracts.txt` | reasoning, krag_context, job_description |
| 2 | `verify_data_flow.txt` | reasoning, krag_context, job_description |
| 3 | `verify_sketch.txt` | reasoning, contract_sheet, data_flow_map, job_description |
| 4 | `verify_patterns.txt` | reasoning, krag_context, job_description |
| 5 | `verify_assumptions.txt` | reasoning, contract_sheet, data_flow_map, feasibility_report, pattern_catalog, job_description |
| 6 | `verify_type_boundaries.txt` | reasoning, krag_context, job_description |

Templates use `str.format()` with named placeholders matching the method
parameters.

### Failure Isolation

Each agent is individually wrapped in try/except. A single agent failure (LLM
timeout, malformed response, connection error) does not abort the verification
phase or the pipeline. The specific behaviors:

- **Individual agent exception**: logged as warning, returns empty string.
  Other agents continue normally.
- **asyncio.gather exception**: `return_exceptions=True` captures exceptions as
  results instead of propagating them. The orchestrator checks `isinstance(r,
  Exception)` for each result.
- **All agents fail**: `_run_verification_agents()` returns empty string.
  The pipeline continues without verification findings -- the self-critique
  and extraction phases operate on the raw reasoning alone.

### Adaptive Context Delivery

The verification findings are injected INTO the architecture reasoning prompt
(when using adaptive context delivery), not into the self-critique prompt. This
is deliberate: findings in the reasoning prompt help the model ground its
analysis in verified facts. Findings in the critique prompt make the critic
over-aggressive -- it treats new proposals as hallucinations because they
differ from the verified existing code.

## Key Design Decisions

1. **Parallel + sequential, not all parallel** -- agents 3 and 5 depend on
   earlier agents' outputs to produce meaningful results. Running them in
   parallel would mean they operate without the contract sheet and data flow
   map, reducing their effectiveness to near zero.

2. **max_tokens=4096 per agent** -- focused output budget. Verification agents
   should produce concise, specific findings (method signatures, data flow
   paths, type mismatches), not lengthy analysis. 4096 tokens is enough for
   thorough verification while preventing rambling.

3. **Findings injected into reasoning, not returned separately** -- the
   self-critique pass and per-field extraction operate on the reasoning text.
   By appending findings to the reasoning, they become visible to all
   downstream consumers without changing any interfaces.

4. **Skip when no codebase context** -- verification against imaginary code is
   worse than no verification. If agent context gathering failed or the job
   has no source directory, the agents are skipped entirely.

5. **Non-fatal by design** -- verification is a quality improvement, not a
   correctness requirement. The pipeline must complete even if all six agents
   fail. This is why every agent has its own try/except and the orchestrator
   tolerates all-empty results.

6. **Six agents, not one big verification call** -- each agent has a focused
   task with a specific prompt template. A single "verify everything" prompt
   would exceed the context window and produce shallow analysis across all
   dimensions. Dedicated agents go deeper on their specific concern.

## Configuration

No user-facing configuration. Verification agents run automatically during
the Architecture+Design stage whenever gathered codebase context is available.
They cannot be disabled individually.

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/pipeline/stages/architecture_design.py` | `_run_verification_agents()` orchestrator and all 6 `_verify_*()` methods |
| `fitz_forge/planning/prompts/verify_contracts.txt` | Agent 1 prompt template |
| `fitz_forge/planning/prompts/verify_data_flow.txt` | Agent 2 prompt template |
| `fitz_forge/planning/prompts/verify_sketch.txt` | Agent 3 prompt template |
| `fitz_forge/planning/prompts/verify_patterns.txt` | Agent 4 prompt template |
| `fitz_forge/planning/prompts/verify_assumptions.txt` | Agent 5 prompt template |
| `fitz_forge/planning/prompts/verify_type_boundaries.txt` | Agent 6 prompt template |

## Related Features

- [Per-Field Extraction](per-field-extraction.md) -- consumes the combined
  reasoning+verification text for field extraction
- [Split Reasoning](split-reasoning.md) -- verification runs on combined output
  regardless of split mode
- [Grounding Validation](grounding-validation.md) -- post-synthesis AST-level
  validation that complements the pre-extraction verification agents
