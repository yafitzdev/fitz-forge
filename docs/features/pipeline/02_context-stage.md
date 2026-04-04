# Context Stage

## Problem

Before making any architectural decisions, the planning system needs to understand
what is being built: the project description, concrete requirements, constraints,
scope boundaries, stakeholders, existing files, needed artifacts, and the assumptions
the model is making. Small local LLMs cannot reliably extract all of these fields in
a single large JSON response. A 3B model asked to produce a 2000-token JSON blob will
hallucinate fields, nest them incorrectly, or truncate the output.

## Solution

Stage 1 of the planning pipeline uses a reasoning-then-extraction pattern. One
free-form reasoning call with file access tools lets the LLM think through the project
context. A self-critique pass catches errors in the reasoning. Then 4 small JSON
extractions -- each under 2000 characters -- pull individual field groups from the
reasoning text. Small models handle these tiny schemas reliably. Failed groups get
Pydantic defaults rather than crashing the stage.

## How It Works

### Progress Range

0.10 to 0.25 of overall pipeline progress.

### Execute Flow

The `ContextStage.execute` method runs 5 sequential steps:

1. **Reasoning with tool access** -- `_reason_with_tools` sends the context prompt
   (formatted from `context.txt`) to the LLM. The prompt includes the raw summaries
   from agent context gathering and the implementation check result (if available).
   During reasoning, the LLM can call `inspect_files` and `read_file` tools to examine
   specific source files on demand.

2. **Self-critique** -- `_self_critique` sends the reasoning output back to the LLM
   with a critique prompt and the gathered codebase context. The LLM identifies scope
   inflation, hallucinated files, missed existing code, and other errors, then produces
   a corrected reasoning text.

3. **Per-field-group extraction** -- 4 independent extraction calls, each producing a
   tiny JSON object:

   | Group          | Fields                                                          |
   |----------------|-----------------------------------------------------------------|
   | `description`  | `project_description`, `key_requirements`, `constraints`, `existing_context` |
   | `stakeholders` | `stakeholders`, `scope_boundaries` (in_scope, out_of_scope)    |
   | `files`        | `existing_files`, `needed_artifacts`                            |
   | `assumptions`  | `assumptions` (array of assumption/impact/confidence objects)   |

   The `description` and `files` groups receive codebase context as `extra_context`
   for accurate path identification. The `stakeholders` and `assumptions` groups do
   not, since they derive from the reasoning text alone.

4. **Post-extraction validators** -- `ensure_min_existing_files` checks that the
   extracted `existing_files` list is not suspiciously empty when the agent gathered
   codebase context. If so, it supplements from the gathered file list.

5. **Pydantic validation** -- The merged dict is passed through `ContextOutput` which
   applies type coercion, default values, and validation constraints.

### Prompt Construction

`build_prompt` loads the `context.txt` template and formats it with:

- `description` -- The user's job description.
- `krag_context` -- Raw summaries from agent context gathering (per-file detail for
  accurate file identification during reasoning).

If an implementation check result exists, it is prepended to the prompt so the LLM
knows upfront whether the task already has code in the codebase.

### Error Handling

If any step raises an exception, the entire stage returns `StageResult(success=False)`
with the error message. The orchestrator logs the failure and halts the pipeline.

## Key Design Decisions

1. **Reasoning before extraction.** The LLM first thinks freely, then structured
   extraction pulls fields from its own reasoning. This separates "thinking" from
   "formatting" -- a critical distinction for small models.
2. **4 groups, not 1 monolithic extraction.** Each extraction is under 2000 characters
   of schema. A 3B model at Q3 quantization can reliably produce JSON at this scale.
3. **Selective codebase context.** Only `files` and `description` groups need the
   structural index for accurate path references. Feeding context to all groups wastes
   tokens and can confuse the model.
4. **Raw summaries for reasoning, synthesized for extraction.** The reasoning pass gets
   raw summaries (per-file detail) for depth. Extraction calls get the synthesized
   structural overview for compact grounding.
5. **Post-extraction validators over prompt engineering.** Rather than hoping the LLM
   lists enough existing files, `ensure_min_existing_files` programmatically backfills
   from the agent's file list. Deterministic fix beats probabilistic prompting.

## Configuration

No stage-specific configuration. The context stage inherits settings from the pipeline
orchestrator (LLM client, checkpoint manager, progress callbacks).

## Files

| File                                              | Role                                    |
|---------------------------------------------------|-----------------------------------------|
| `fitz_forge/planning/pipeline/stages/context.py`  | Stage implementation                    |
| `fitz_forge/planning/schemas/context.py`          | `ContextOutput` Pydantic model          |
| `fitz_forge/planning/prompts/context.txt`         | Reasoning prompt template               |
| `fitz_forge/planning/pipeline/validators.py`      | `ensure_min_existing_files` validator   |
| `fitz_forge/planning/pipeline/stages/base.py`     | Base class with tool access, critique   |

## Related Features

- **Agent Context Gathering** -- Produces the `raw_summaries` and `_gathered_context`
  consumed by this stage's prompt and extraction calls.
- **Implementation Check** -- Result is prepended to the reasoning prompt so the LLM
  knows if the task is already built.
- **Architecture-Design Stage** -- Consumes context output (requirements, constraints,
  scope boundaries, existing files, needed artifacts) as binding constraints.
- **Artifact Duplicate Check** -- Triggered after context extraction when
  `needed_artifacts` is non-empty. Searches the full structural index for existing
  files matching proposed names.
