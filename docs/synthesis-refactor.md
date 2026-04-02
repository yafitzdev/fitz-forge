# Synthesis Prompt Refactor

## Problem

The synthesis reasoning prompt sends ALL resolved decisions (full text + reasoning + evidence + constraints) in one shot. For fitz-sage (14 decisions), this is 39K chars (~10K tokens). A larger codebase with 30 decisions could hit 80K+ chars — blowing past the 32K token context budget.

## Root Cause

`_format_resolutions()` dumps the full resolution output including the LLM's internal reasoning chain. The synthesis model doesn't need to know WHY a decision was made — it needs WHAT was decided and WHAT the constraints are.

## Measured Sizes (fitz-sage, 14 decisions)

```
Component                        Chars    Tokens (est)
─────────────────────────────────────────────────────
Original decisions (full)        38,734   ~9,700
Compact (no reasoning)           17,629   ~4,400   (-54%)
Minimal (decision+constraints)   12,285   ~3,100   (-68%)
Gathered context                 20,894   ~5,200
Template + instructions           2,412   ~600
─────────────────────────────────────────────────────
CURRENT total prompt             62,040   ~15,500
WITH compact decisions           40,935   ~10,200  (-34%)
```

## Solution: Two Changes

### Change 1: Compact Resolution Format

Drop the `reasoning` field from the synthesis prompt. Keep:
- Decision text (what was decided)
- Evidence signatures (file:method — no explanation after `--`)
- Constraints (binding rules for downstream)

Savings: 54% on the decisions section, 34% on total prompt.

### Change 2: Sectioned Extraction

Currently all field groups (context, architecture, design, roadmap, risk) extract from the same reasoning output in parallel. But not all sections need the same input:

```
Section          Needs                                    Source
──────────────────────────────────────────────────────────────────
Context          All decisions + gathered_context          Reasoning output
Architecture     All decisions + gathered_context          Reasoning output
Design           All decisions + gathered_context          Reasoning output
Roadmap          Design output + constraints-only          Design extraction output
Risk             Design output + constraints-only          Design extraction output
```

New extraction flow:
1. Synthesis reasoning call (compact decisions + gathered_context)
2. Extract Context, Architecture, Design in parallel (from reasoning)
3. Extract Roadmap, Risk in parallel (from reasoning + Design output + decision constraints)

Roadmap/Risk context: ~5K tokens (slim Design output without artifact code + constraints-only).

### Token Budget (32K target)

```
Prompt                              Current    After refactor
────────────────────────────────────────────────────────────
Synthesis reasoning                 ~15.5K     ~10.2K tokens
Per-field extraction (each)         ~4-6K      ~4-6K tokens (unchanged)
Roadmap/Risk extraction             ~4-6K      ~5K tokens (Design + constraints)
Per-artifact generation             ~3K        ~3K tokens (unchanged)
────────────────────────────────────────────────────────────
Max single prompt                   ~15.5K     ~10.2K tokens
```

All prompts comfortably within 32K budget even for codebases 2-3x larger.

### Implementation Plan

**Phase 1: Compact decisions (quick win, no flow change)** -- DONE
- Modify `_format_resolutions()` to drop reasoning, truncate evidence to signatures
- All existing extraction paths benefit immediately
- Benchmark to verify no quality regression

**Phase 2: Sectioned extraction (flow change)** -- DONE
- Modify `execute()` to extract Context+Architecture+Design first
- Then extract Roadmap+Risk with Design output injected
- Build `_format_constraints_only()` for roadmap/risk context
- Build `_slim_design_output()` to strip artifact code bodies
- Benchmark to verify quality on roadmap/risk sections

**Phase 3: Best-of-2 selection at compounding bottlenecks** -- DONE
- Decision decomposition: generate 2 candidates (temp=0.3), score deterministically, pick best
- Synthesis reasoning: generate 2 candidates (temp=0.7), score deterministically, pick best
- Scoring criteria: graph coverage, question specificity, file refs, section coverage, concreteness
- Extra cost: ~50-70s per plan, but eliminates floor plans from compounding errors

### Caps and Guardrails

| Parameter | Current | Target | Rationale |
|-----------|---------|--------|-----------|
| Interface injection | 50 lines | 50 lines | Already capped |
| Source code in artifact prompt | 6000 chars | 6000 chars (line-boundary) | Fix mid-line truncation |
| Reasoning in artifact prompt | 3000 chars | 3000 chars (line-boundary) | Fix mid-sentence truncation |
| Decisions per artifact | 6 | dynamic (all relevant, cap 10) | Don't drop relevant decisions |
| Resolution format | full | compact (no reasoning) | Drop 54% of tokens |
| Roadmap/Risk decisions | all | constraints-only + slim design | Right-sized context |

### Files to Modify

- `fitz_forge/planning/pipeline/stages/synthesis.py`
  - `_format_resolutions()` — compact format
  - `execute()` — sectioned extraction flow
  - `_generate_single_artifact()` — fix line-boundary truncation
  - `_filter_decisions_for_file()` — raise cap from 6 to dynamic
- `fitz_forge/planning/pipeline/stages/base.py`
  - `_extract_field_group()` — accept extra context for roadmap/risk
