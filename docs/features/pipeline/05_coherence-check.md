# Coherence Check

## Problem

The planning pipeline runs 3 stages sequentially, each producing structured output
grounded in the previous stage's results. Despite binding constraints and self-critique,
cross-stage inconsistencies still emerge: a roadmap that implements a different approach
than the architecture recommended, risks referencing phase numbers that do not exist,
component names that appear in the design but never in any roadmap deliverable, or
needed artifacts from the context stage missing from the roadmap entirely. Each stage
is internally consistent but the plan as a whole may contradict itself.

## Solution

A single cross-stage coherence check that runs after all three stages complete. One LLM
call receives a concise summary of every stage's key outputs and checks 5 specific
consistency properties. The check returns either "COHERENT" or a JSON dict of targeted
fixes. Only scalar field fixes are applied -- list fields (phases, risks, approaches)
are protected because the LLM routinely truncates arrays in its fix output.

## How It Works

### Placement in the Pipeline

The coherence check runs at progress 0.955, after the roadmap-risk stage (0.65-0.95)
and before confidence scoring. It is implemented as the `_coherence_check` method on
`PlanningPipeline` in the orchestrator.

### Input Construction

The check builds a concise summary of each stage's key outputs:

- **CONTEXT**: key requirements, constraints, needed artifacts.
- **ARCHITECTURE**: recommended approach, reasoning.
- **DESIGN**: component names, ADR titles.
- **ROADMAP**: phases (number, name, deliverables), critical path.
- **RISKS**: risk descriptions with affected phases, overall risk level.

This summary is deliberately compact. Full stage outputs would exceed the context
window; the check only needs the fields involved in cross-stage references.

### 5 Consistency Checks

The LLM is instructed to verify:

1. **Requirements coverage** -- Every requirement from CONTEXT is addressed by at
   least one roadmap phase's deliverables.
2. **Architecture alignment** -- Roadmap phases implement the architecture's
   recommended approach, not an alternative that was explored but rejected.
3. **Phase reference validity** -- Risk `affected_phases` arrays reference phase
   numbers that actually exist in the roadmap.
4. **Component consistency** -- Component names from DESIGN appear in at least one
   roadmap deliverable or phase description.
5. **Artifact completeness** -- Needed artifacts from CONTEXT appear in roadmap
   deliverables.

### Response Handling

Two response formats are accepted:

- **"COHERENT"** -- The string "COHERENT" (case-insensitive, checked in first 50
  characters) means all checks pass. No fixes applied.
- **JSON fixes** -- A dict mapping section names to fix dicts:
  ```json
  {"roadmap": {"total_phases": 4}, "risk": {"overall_risk_level": "high"}}
  ```

### Protected Keys

The following keys are never replaced by coherence fixes, even if the LLM suggests
changes:

- `risks`, `phases`, `approaches`, `adrs`, `components`
- `key_requirements`, `constraints`, `deliverables`

These are all list/array fields. The LLM's coherence fix routinely returns truncated
arrays (e.g., 2 phases instead of 5) because it summarizes rather than reproduces.
Applying such a "fix" would destroy data. Only scalar fields (strings, numbers,
booleans) are safe to update.

### Fix Application

When fixes are accepted:

1. The section is located in `prior_outputs` (e.g., `prior_outputs["risk"]`).
2. Protected keys are filtered out.
3. Remaining scalar fixes are applied via `dict.update()`.
4. The nested stage output is also updated (e.g.,
   `prior_outputs["roadmap_risk"]["risk"]`) to maintain consistency between the
   flat and nested views.

### Failure Handling

On any exception (LLM timeout, malformed JSON, unexpected response format), the
method returns `{}` (empty fixes). The pipeline continues with the original stage
outputs. A failed coherence check never crashes the pipeline.

## Key Design Decisions

1. **Single LLM call, not per-pair checks.** Checking context-vs-architecture,
   architecture-vs-roadmap, and roadmap-vs-risk separately would triple the latency.
   One call with all summaries is sufficient because the check is lightweight.
2. **Protected list fields.** This is the single most important safeguard. Early
   versions applied all fixes, and the coherence check regularly destroyed phases and
   risks by returning truncated arrays. Protecting list fields and only applying scalar
   fixes eliminated this failure mode entirely.
3. **Concise summary over full outputs.** Sending full stage outputs to the coherence
   check would exceed context limits on small models. The summary includes only the
   cross-reference fields (names, numbers, deliverables) needed for consistency checks.
4. **Post-pipeline, not inter-stage.** Running coherence checks between stages would
   require backtracking (re-running an earlier stage if inconsistent with a later one).
   A single post-pipeline check is simpler and the fixes are surgical.
5. **"COHERENT" as fast path.** Checking the first 50 characters for "COHERENT" avoids
   JSON parsing overhead when no fixes are needed, which is the common case.

## Configuration

No dedicated configuration. The coherence check always runs when all stages complete
successfully.

## Files

| File                                            | Role                                      |
|-------------------------------------------------|-------------------------------------------|
| `fitz_forge/planning/pipeline/orchestrator.py`  | `_coherence_check` method                 |
| `fitz_forge/planning/pipeline/stages/base.py`   | `SYSTEM_PROMPT`, `extract_json` utility   |

## Related Features

- **Context Stage** -- Produces requirements and needed artifacts that the check
  verifies are addressed downstream.
- **Architecture-Design Stage** -- Produces the recommended approach and component
  names that the check verifies are implemented in the roadmap.
- **Roadmap-Risk Stage** -- Produces phases and risks that the check verifies are
  internally consistent and aligned with upstream decisions.
- **Confidence Scoring** -- Runs after coherence fixes are applied, scoring the
  corrected plan sections.
- **Plan Renderer** -- Renders the final plan from the coherence-checked outputs.
