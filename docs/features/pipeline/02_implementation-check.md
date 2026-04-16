# Implementation Check

## Problem

Local LLMs frequently propose building features that already exist in the codebase.
A model receiving a task like "add token tracking" will produce a full architecture
plan with new files, new schemas, and a multi-phase roadmap -- even when the codebase
already has a complete `token_tracking.py` module. Every downstream stage (context,
architecture, design, roadmap, risk) inherits this fundamental misunderstanding,
producing a plan that is structurally valid but completely wrong.

## Solution

A single surgical LLM call between agent context gathering and the first planning stage.
The implementation check receives the synthesized codebase context and the job
description, then returns a structured verdict: is this task already implemented, what
is the evidence, and what gaps remain. This result is injected into `prior_outputs` so
every downstream stage starts from ground truth about the current state of the code.

## How It Works

### Placement in the Pipeline

The check runs at progress 0.092, after agent context gathering (0.06-0.09) and before
decision decomposition (0.10-0.20). It is implemented as the `_implementation_check`
method on `PlanningPipeline` and called by the orchestrator, not as a standalone stage.

### Single LLM Call

The check uses the `implementation_check.txt` prompt template formatted with two
variables:

- `job_description` -- The user's original planning request.
- `synthesized_context` -- The structural overview from agent context gathering
  (interface signatures, library signatures, structural index of selected files).

The LLM receives the system prompt (senior architect persona) and returns a JSON
response.

### Return Schema

```json
{
  "already_implemented": true,
  "evidence": "fitz_forge/planning/agent/gatherer.py contains AgentContextGatherer with full retrieval pipeline",
  "gaps": ["Missing retry logic on LLM call failures"]
}
```

- **`already_implemented`** (bool) -- Whether the task's core functionality exists.
- **`evidence`** (str) -- Specific files, classes, or functions that prove the claim.
- **`gaps`** (list[str]) -- Remaining work even if partially implemented. Empty list
  means fully implemented.

### Injection into Pipeline State

The result is stored at `prior_outputs["_implementation_check"]`. The leading underscore
marks it as internal metadata (not a stage output). Every stage's `build_prompt` method
calls `self._get_implementation_check(prior_outputs)` which formats the check result
into a text block prepended to the stage prompt. This means the LLM sees
"IMPLEMENTATION STATUS: Already implemented. Evidence: ..." before the planning
instructions.

### Failure Handling

On any exception (LLM timeout, malformed JSON, unexpected structure), the check returns
the safe default:

```python
{"already_implemented": False, "evidence": "", "gaps": []}
```

This means a failed check never crashes the pipeline. The worst case is that downstream
stages do not get the implementation signal, which is the same as not running the check
at all.

### Checkpoint Behavior

The check result is not persisted as a separate checkpoint. It is stored in
`prior_outputs` which gets checkpointed when the first real stage completes. If the
pipeline resumes from checkpoint and `_implementation_check` already exists in
`prior_outputs`, the check is skipped.

## Key Design Decisions

1. **Single call, not a stage.** The check is too small to justify a full stage with
   reasoning/critique/extraction. One LLM call with a focused prompt is sufficient.
2. **Safe default on failure.** Returning `already_implemented: False` is conservative.
   A false negative (missing that code exists) is better than crashing the pipeline.
3. **Synthesized context, not raw summaries.** The synthesized context includes
   interface signatures and structural index -- enough to detect existing implementations
   without the noise of full file contents.
4. **Prepend to prompt, not a separate section.** The implementation status is injected
   before the stage instructions so the LLM processes it first and anchors its reasoning.
5. **Gaps field enables partial detection.** A task can be "already implemented" with
   gaps, letting downstream stages focus on the remaining work rather than replanning
   everything from scratch.

## Configuration

No dedicated configuration. The check runs whenever agent context gathering produces
a non-empty `_gathered_context` and `_implementation_check` is not already present in
`prior_outputs`.

## Files

| File                                            | Role                                      |
|-------------------------------------------------|-------------------------------------------|
| `fitz_forge/planning/pipeline/orchestrator.py`  | `_implementation_check` method            |
| `fitz_forge/planning/prompts/implementation_check.txt` | Prompt template                  |
| `fitz_forge/planning/pipeline/stages/base.py`   | `_get_implementation_check` helper        |

## Related Features

- [Agent Context Gathering](01_agent-context-gathering.md) — produces the
  `synthesized` context that the implementation check consumes.
- [Decision Decomposition](04_decision-decomposition.md) — first consumer of
  the check result; decomposes around verification and extension rather than
  building from scratch when the task is already implemented.
- [Synthesis](06_synthesis.md) — the check is also injected into synthesis
  reasoning so the final plan acknowledges existing implementations.
