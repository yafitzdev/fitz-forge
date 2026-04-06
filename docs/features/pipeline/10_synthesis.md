# 10 — Synthesis

## Problem

After decisions are resolved individually, the results are a bag of per-decision records — not a coherent plan. Each resolution knows about its own 1-3 files but not the full picture. The final plan needs unified context, architecture, design, roadmap, and risk sections that are internally consistent and reference each other correctly.

## Solution

The synthesis stage narrates pre-solved decisions into the standard plan format. The model isn't discovering anything new — all decisions are already committed with constraints. It's assembling a coherent document from resolved facts. This uses the same per-field extraction infrastructure as the classic pipeline stages, producing the same `PlanOutput` format.

## How It Works

### Input

The synthesis stage receives:
- All committed decision resolutions with their constraints
- The call graph for structural awareness
- File manifest and agent context
- Implementation check results (if any)

### Reasoning Pass

A reasoning prompt injects all decision resolutions as context. The model's job is to narrativize — organize the individual answers into a coherent architecture story, identify the right approach from the committed decisions, and produce a unified analysis.

The reasoning uses **best-of-3 synthesis**: three independent reasoning attempts are generated, then the best one is selected based on scope consensus (the attempt closest to the median scope is preferred, avoiding both scope inflation and scope deflation).

### Per-Field Extraction

After reasoning, the same field group extraction runs as in the classic pipeline — but now for all five plan sections in one stage:

- **Context fields** (4 groups): project description, stakeholders, files, assumptions
- **Architecture fields** (2 groups): approaches + tradeoffs
- **Design fields** (4 groups): ADRs, components, integrations, artifacts
- **Roadmap fields** (1 group): phases with verification commands
- **Risk fields** (1 group): risks with mitigations

Each extraction is <2000 chars — the same per-field extraction that makes small models reliable.

### Per-Artifact Generation

Artifacts (code templates, config files) are generated individually rather than in a batch. Each artifact gets its own LLM call with:
- The reasoning context
- Class interface signatures (AST-extracted) for the relevant files
- Imported type API reference
- Deterministic repair rules to fix common fabrication patterns

### Deterministic Repair

After artifact generation, each artifact goes through deterministic repair:
- **F9 — Reference method injection**: Stubs for real implementations replace fabricated ones
- **F10 — Service API fabrication**: Imported type APIs are injected so the model uses real methods
- **F12 — Filename cleanup**: Strip method suffixes, reject invalid names
- **F13 — Approach fallback**: Derive approach from key_tradeoffs when reasoning produces an empty approach

### Output

The synthesis stage produces the full `PlanOutput`:

```json
{
  "context": { "project_description": "...", "key_requirements": [...], ... },
  "architecture": { "approaches": [...], "recommended": "...", ... },
  "design": { "adrs": [...], "components": [...], "artifacts": [...], ... },
  "roadmap": { "phases": [...], "critical_path": [...], ... },
  "risk": { "risks": [...], "overall_risk_level": "...", ... }
}
```

This is the same format as the classic pipeline, making both pipelines interchangeable for downstream rendering and confidence scoring.

## Key Design Decisions

1. **Narration, not reasoning.** The model assembles committed facts into a document. It doesn't re-reason about architecture choices — those were made during resolution with focused evidence. This keeps the synthesis reliable even on small models.

2. **Best-of-3 for scope calibration.** Three reasoning attempts with scope consensus selection prevents the two most common failure modes: scope inflation (proposing features beyond the task) and scope deflation (missing required deliverables).

3. **Per-artifact generation.** Each artifact gets its own call with tailored context (class interfaces, imported types). Batch artifact generation overwhelms small models — they lose track of which file they're generating.

4. **Deterministic repair over LLM correction.** Fabricated method names are fixed by deterministic string matching against the structural index, not by asking the LLM to fix its own mistakes. The LLM is bad at correcting itself; pattern matching is reliable.

5. **Same output format as classic pipeline.** The decomposed pipeline is a drop-in replacement for the classic pipeline. Downstream rendering, confidence scoring, and API review work identically regardless of which pipeline produced the plan.

## Configuration

No user-facing configuration. The synthesis stage runs automatically as the final stage of the `DecomposedPipeline`.

| Internal | Value | Description |
|----------|-------|-------------|
| Progress range | 0.75–0.94 | Final assembly before post-processing |
| Best-of-N | 3 | Independent reasoning attempts for scope consensus |

## Files

| File | Description |
|------|-------------|
| `fitz_forge/planning/pipeline/stages/synthesis.py` | `SynthesisStage` — the largest stage (~3000 lines) |
| `fitz_forge/planning/schemas/` | All output schemas (ContextOutput, ArchitectureOutput, etc.) |
| `fitz_forge/planning/prompts/synthesis*.txt` | Prompt templates for synthesis reasoning and extraction |

## Related Features

- [Decision Resolution](09_decision-resolution.md) — produces the committed decisions synthesized here
- [Per-Field Extraction](../infrastructure/per-field-extraction.md) — the extraction mechanism used for all 12 field groups
- [Grounding Validation](../infrastructure/grounding-validation.md) — validates artifacts after synthesis
- [Coherence Check](05_coherence-check.md) — runs after synthesis to verify cross-section consistency
