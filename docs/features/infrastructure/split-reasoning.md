# Split Reasoning

## Problem

The merged Architecture+Design stage produces a single reasoning prompt that
combines codebase context, prior stage outputs, binding constraints, and task
description. For real codebases, this prompt reaches ~29K tokens. Dense 27B
models running at 32K context cannot fit this prompt plus a meaningful response
window -- the model either truncates its reasoning or enters a context-shift
loop where llama-server discards old tokens and the model loses coherence.

The same problem affects the Roadmap+Risk stage, where the roadmap reasoning
prompt includes the full architecture decision, design components, ADRs, and
codebase context.

## Solution

Split merged stages into two sequential LLM calls, each using approximately
8K tokens of context instead of 29K. The first call's output is injected into
the second call's prompt, preserving information flow without requiring both
halves to fit in memory simultaneously.

- **Architecture+Design**: architecture reasoning first, then design reasoning
  with the architecture decision injected
- **Roadmap+Risk**: roadmap reasoning first, then risk reasoning with the
  roadmap injected

Split mode is auto-enabled when the configured `context_length` is below
32768 tokens, making it transparent to the user.

## How It Works

### Architecture+Design Split

When `split_reasoning=True`, `ArchitectureDesignStage.execute()` calls
`_execute_split()` instead of `_execute_combined()`:

1. **Architecture reasoning** -- `_build_split_architecture_prompt()` loads the
   `architecture.txt` prompt template with context, krag_context, and binding
   constraints. The model produces a free-form architecture analysis: approaches,
   tradeoffs, and a recommended approach.

2. **Design reasoning** -- `_build_split_design_prompt()` loads the `design.txt`
   prompt template. The architecture reasoning from step 1 is injected via the
   `{architecture}` template variable. The model produces design decisions
   (components, ADRs, data model, artifacts) grounded in the chosen approach.

3. **Combine** -- the two reasoning outputs are concatenated under
   `## Architecture Analysis` and `## Design Decisions` headers. All downstream
   processing (verification agents, self-critique, per-field extraction)
   operates on this combined text identically to the non-split path.

### Roadmap+Risk Split

`RoadmapRiskStage._execute_split()` follows the same pattern:

1. **Roadmap reasoning** -- `_build_split_roadmap_prompt()` loads `roadmap.txt`
   with context, architecture+design summary, krag_context, and binding
   constraints.

2. **Risk reasoning** -- `_build_split_risk_prompt()` loads `risk.txt` with the
   same inputs plus the roadmap reasoning injected via `{roadmap}`.

3. **Combine** -- concatenated under `## Roadmap` and `## Risk Assessment`.
   Self-critique and per-field extraction run on the combined output.

### Automatic Activation

The `create_stages()` factory function accepts a `split_reasoning` parameter:

```python
def create_stages(*, split_reasoning: bool = False) -> list[PipelineStage]:
    return [
        ContextStage(),
        ArchitectureDesignStage(split_reasoning=split_reasoning),
        RoadmapRiskStage(split_reasoning=split_reasoning),
    ]
```

The background worker reads `context_length` from the configuration and enables
split mode when the value is below 32768. The context stage is unaffected
because its prompts are small enough to fit in any supported context window.

### Shared Prompt Parts

Both stages extract shared prompt components through `_build_prompt_parts()` to
avoid duplication between split and combined modes. This method returns a tuple
of (context string, architecture/design string, binding constraints, krag
context) that both code paths consume.

### Post-Reasoning Consistency

Verification agents and self-critique run on the combined output regardless of
whether split mode was used. This means:

- Verification agents see the full architecture+design reasoning as a single
  text, so they can check cross-cutting concerns (e.g., a design component that
  contradicts the architecture decision)
- Self-critique evaluates the combined reasoning for scope inflation and
  hallucinated files, catching issues that span both halves
- Per-field extraction operates on the same combined text, so mini-schemas
  can reference information from either half

## Key Design Decisions

1. **Two calls, not a sliding window** -- rather than using a longer context
   window with attention tricks, split reasoning uses two focused calls. Each
   call has full attention over its ~8K tokens, producing higher quality output
   than a single 29K call with degraded attention at the edges.

2. **Architecture informs design, not vice versa** -- the split order is not
   arbitrary. Architecture decisions (approach, tradeoffs) must be finalized
   before design details (components, ADRs, artifacts) can be grounded. This
   matches the natural dependency flow.

3. **Auto-detection over manual toggle** -- the worker enables split mode from
   `context_length` in config. Users who change models or quantization levels
   do not need to remember to toggle a flag.

4. **No quality loss at 5 seed files** -- benchmarked at 5/5 correct
   architecture decisions with 5 seed files in split mode. The quality loss
   from splitting is negligible because each call has better attention quality
   over a smaller context window.

5. **Context stage excluded** -- the context stage prompt is small (typically
   under 8K tokens even with full codebase context) and does not benefit from
   splitting. Adding split logic there would add complexity without benefit.

## Configuration

| Setting | Location | Effect |
|---------|----------|--------|
| `context_length` | `config.yaml` | When < 32768, split reasoning auto-enables |
| `split_reasoning` | `create_stages()` kwarg | Direct programmatic control |

No CLI flag exists. The worker handles detection automatically.

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/pipeline/stages/architecture_design.py` | `_execute_split()`, `_build_split_architecture_prompt()`, `_build_split_design_prompt()` |
| `fitz_forge/planning/pipeline/stages/roadmap_risk.py` | `_execute_split()`, `_build_split_roadmap_prompt()`, `_build_split_risk_prompt()` |
| `fitz_forge/planning/pipeline/stages/__init__.py` | `create_stages(split_reasoning=)` factory function |
| `fitz_forge/planning/prompts/architecture.txt` | Architecture-only reasoning prompt template |
| `fitz_forge/planning/prompts/design.txt` | Design-only reasoning prompt template (has `{architecture}` slot) |
| `fitz_forge/planning/prompts/roadmap.txt` | Roadmap-only reasoning prompt template |
| `fitz_forge/planning/prompts/risk.txt` | Risk-only reasoning prompt template (has `{roadmap}` slot) |

## Related Features

- [Per-Field Extraction](per-field-extraction.md) -- the extraction phase that
  consumes the combined reasoning output from split mode
- [LLM Providers](llm-providers.md) -- the `context_length` config that
  triggers auto-detection
- [Verification Agents](verification-agents.md) -- run on combined output
  regardless of split mode
