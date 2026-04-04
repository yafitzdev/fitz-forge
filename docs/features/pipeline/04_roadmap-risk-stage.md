# Roadmap-Risk Stage

## Problem

An architectural plan without an implementation roadmap is aspirational, not actionable.
The planning system needs to translate the chosen architecture and design decisions into
concrete phases with deliverables, dependencies, effort estimates, and verification
commands. Simultaneously, it must identify risks tied to specific phases with concrete
mitigations. Small local LLMs produce dependency cycles (phase 3 depends on phase 5),
string-format phase numbers (`"Phase 1"` instead of `1`), and generic risks
("something might fail") unless constrained.

## Solution

Stage 3 of the pipeline combines roadmap and risk assessment into a merged stage with
3 per-field-group extractions and programmatic post-processing. Dependency cycles are
stripped automatically. Phase numbers are coerced from strings to integers. Validators
ensure phase 0 exists, risks reference real phases, and verification commands are
concrete. Like the architecture-design stage, it supports combined and split reasoning
modes.

## How It Works

### Progress Range

0.65 to 0.95 of overall pipeline progress.

### Execute Flow

The `RoadmapRiskStage.execute` method runs these steps:

1. **Reasoning** -- Either combined or split mode:
   - **Combined** (default): Single LLM call using `roadmap_risk.txt` with context
     from all prior stages (project description, requirements, constraints, chosen
     architecture, ADRs, components, integration points, artifacts) and codebase context.
     Tool access is available for on-demand file reading.
   - **Split** (`split_reasoning=True`): Two sequential calls. Roadmap reasoning uses
     `roadmap.txt`, then risk reasoning uses `risk.txt` with the roadmap output injected.
     Reduces peak context for smaller models.

   Self-critique runs on the combined output, checking against codebase context.

2. **Per-field-group extraction** -- 3 independent extraction calls:

   | Group        | Fields                                                             |
   |--------------|--------------------------------------------------------------------|
   | `phases`     | `phases` (array of phase objects with number, name, objective, deliverables, dependencies, estimated_complexity, key_risks, verification_command, estimated_effort) |
   | `scheduling` | `critical_path`, `parallel_opportunities`, `total_phases`          |
   | `risks`      | `risks` (array with category, description, impact, likelihood, mitigation, contingency, affected_phases, verification), `overall_risk_level`, `recommended_contingencies` |

   The `phases` and `risks` groups receive codebase context for grounding verification
   commands in actual test files and risks in actual code structure.

3. **Post-extraction validators**:
   - `ensure_phase_zero` -- Guarantees a phase 0 (setup/foundation) exists if the
     plan has multiple phases.
   - `ensure_grounded_risks` -- Checks that `affected_phases` in each risk reference
     phase numbers that actually exist in the roadmap.
   - `ensure_concrete_verification` -- Validates that `verification_command` fields
     contain executable commands (not descriptions), using an LLM call to fix vague
     commands.

4. **Pydantic validation** -- Split into `RoadmapOutput` and `RiskOutput`.

### Dependency Cycle Removal

`_remove_dependency_cycles` enforces a strict rule: dependencies must point to earlier
phases only. For each phase, any dependency where `dep_num >= phase_num` or `dep_num`
does not exist in the phase list is stripped. This runs after phase number coercion so
string formats like `"Phase 1"` are already normalized to integers.

The coercion is handled by `PhaseRef` in the Pydantic schema, which accepts both
integer and string phase references and normalizes them.

### Prior Stage Consumption

The prompt includes structured summaries of all upstream outputs:

- **Context**: project description, requirements, constraints, scope boundaries.
- **Architecture**: recommended approach, reasoning, key tradeoffs.
- **Design**: ADRs (title + decision + rationale), components (name + purpose +
  interfaces), integration points, design artifacts.

These are assembled in `_build_prompt_parts` and formatted into the prompt template
as `context`, `architecture_design`, `binding_constraints`, and `krag_context`.

### Binding Constraints

The stage receives binding constraints from all prior stages:

- Deliverable files from the context stage's `needed_artifacts`.
- The chosen approach from the architecture stage's `recommended`.
- Components to implement from the design stage's `components`.

These prevent the roadmap from drifting away from earlier decisions.

## Key Design Decisions

1. **Automatic cycle removal over prompt engineering.** LLMs produce circular
   dependencies regularly. Programmatic stripping is deterministic and silent.
   The alternative -- asking the LLM to "make sure dependencies only point backward"
   -- fails more often than it succeeds.
2. **Phase number coercion.** The `PhaseRef` Pydantic type accepts `"Phase 1"`, `"1"`,
   and `1` and normalizes all to integer `1`. This handles the most common LLM output
   variations without post-hoc regex.
3. **Concrete verification commands.** The `ensure_concrete_verification` validator
   uses an LLM call to fix vague commands like "check that it works" into executable
   pytest invocations. This is one of the few validators that calls the LLM.
4. **Split risks from roadmap.** The split output `{"roadmap": {...}, "risk": {...}}`
   lets downstream consumers (coherence check, confidence scoring, plan renderer)
   access each independently.
5. **Codebase context for phases and risks only.** The `scheduling` group (critical
   path, parallel opportunities) derives from the reasoning text alone. Adding codebase
   context would waste tokens without improving extraction quality.

## Configuration

| Setting            | Default  | Description                                       |
|--------------------|----------|---------------------------------------------------|
| `split_reasoning`  | `False`  | Use two sequential reasoning calls instead of one |

## Files

| File                                                     | Role                                |
|----------------------------------------------------------|-------------------------------------|
| `fitz_forge/planning/pipeline/stages/roadmap_risk.py`    | Stage implementation                |
| `fitz_forge/planning/schemas/roadmap.py`                 | `RoadmapOutput`, `PhaseRef` models  |
| `fitz_forge/planning/schemas/risk.py`                    | `RiskOutput` model                  |
| `fitz_forge/planning/prompts/roadmap_risk.txt`           | Combined reasoning prompt           |
| `fitz_forge/planning/prompts/roadmap.txt`                | Split roadmap prompt                |
| `fitz_forge/planning/prompts/risk.txt`                   | Split risk prompt                   |
| `fitz_forge/planning/pipeline/validators.py`             | Post-extraction validators          |

## Related Features

- **Context Stage** -- Provides requirements, constraints, needed artifacts, and scope
  boundaries as input.
- **Architecture-Design Stage** -- Provides the chosen approach, ADRs, components, and
  integration points that the roadmap must implement.
- **Coherence Check** -- Verifies that roadmap phases implement the chosen architecture,
  risk `affected_phases` reference real phases, and needed artifacts appear in
  deliverables.
- **Plan Renderer** -- Renders roadmap phases and risk tables into the final markdown
  plan output.
