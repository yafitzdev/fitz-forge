# Pipeline Decomposition Analysis (2026-03-31)

## Experiment Results

### Baseline (fitz_sage codebase, no changes)
- 11 plans scored (1 DNF out of 12 generated)
- **Avg: 37.3/60**, Floor: 28, Ceiling: 45, Stdev: 4.9
- Weakest dimensions: alignment (4.6), implementability (5.0)
- Strongest: scope (7.7), files (7.3), contract (6.9)

### P0: write_artifact Tool (FAILED — REGRESSED)

**Approach A: Decomposed artifact generation (model calls write_artifact per file)**
- Scores: 33, 34, 31 (avg 32.7 vs baseline 37.3)
- **Root cause:** Model can't compose Python code inside JSON tool call arguments as well as in a regular JSON extraction. Writing code inside a `content` parameter of a tool call is harder than producing a JSON blob.
- The tool DID catch fabricated methods (engine.py rejected on first try, then accepted with corrections). But the model's overall artifact quality dropped because the generation mechanism changed.

**Approach B: Attribute rejection in write_artifact (too aggressive)**
- ALL artifacts rejected 2x and force-accepted with bad code
- Model can't process long tool rejection messages well enough to use corrections
- Same problem as enriched repair (run 51) — giving the right answer doesn't mean the model can USE it

**Approach C: Tool-enriched template + write_artifact as diagnostic validator**
- Score: 39 (within baseline variance)
- write_artifact validates AFTER template extraction, logs issues but doesn't reject
- Neutral impact — doesn't help or hurt. The validation is informational only.

**Conclusion:** write_artifact as a CREATION tool doesn't work with 40B models. The model writes better artifacts via template extraction (regular JSON extraction from reasoning text) than via tool call arguments. write_artifact as a diagnostic-only validator is neutral.

### P1: Decomposed Synthesis Reasoning (FAILED — CATASTROPHIC REGRESSION)

**Approach A: Split by decision category (arch vs design)**
- Scores: 15, 34 (avg 24.5 vs baseline 37.3)
- The 15/60 plan targeted governance constraint plugins instead of the engine/synthesizer
- **Root cause:** When design decisions don't include pattern/scope decisions, the model loses the architectural framing entirely and goes off the rails

**Approach B: Chained two-pass (all decisions, sections split)**
- Pass 1: ALL decisions → Context + Architecture sections
- Pass 2: ALL decisions + pass 1 output → Design + Roadmap + Risk
- Scores: 26 (avg 26 vs baseline 37.3)
- Model STILL targeted governance eval pipeline instead of engine
- **Root cause:** The model needs all context in ONE call to maintain coherent reasoning. Even injecting the architecture output as "prior_sections" doesn't prevent the model from going off-track in the second call.

**Conclusion:** The 40B model REQUIRES monolithic synthesis reasoning. Any form of splitting — by category, by section, or by chain — causes the model to lose architectural coherence. This is a fundamental model capability limitation, not a pipeline design issue.

### P0+P1+P2 Combined (SKIPPED)
Both P0 and P1 independently regressed. Combining would compound regressions.

## What Worked vs What Didn't

### Decomposition works for:
- **Decision resolution** (1 decision per call, 1-3 files per call) → contract preservation 7-9
- **Per-field extraction** (1 schema group per call, <2K output) → reliable structured output
- **Contradiction detection** (1 cheap call reviewing compact summaries) → catches some issues

### Decomposition DOESN'T work for:
- **Artifact generation** (1 artifact per tool call) → model writes worse code in tool args
- **Synthesis reasoning** (1 section per call) → model loses architectural framing
- **Post-hoc repair** (1 LLM call per violation) → picks wrong alternatives (proven in runs 45-51)

### Pattern: What determines if decomposition helps
Decomposition helps when:
1. Each sub-task is SELF-CONTAINED (doesn't need global context)
2. The output is SMALL and STRUCTURED (JSON schema <2K chars)
3. The input context is NARROW (1-3 files, not the whole codebase)

Decomposition hurts when:
1. Sub-tasks need CROSS-REFERENCING (design needs architecture framing)
2. The output is CODE (Python inside JSON tool args is hard for the model)
3. The model needs to maintain COHERENT NARRATIVE (synthesis reasoning)

## Current Pipeline LLM Call Map

| # | Call | Type | Decomposable? |
|---|------|------|--------------|
| 1 | Implementation check | ATOMIC | No — already focused |
| 2 | Decision decomposition | MONOLITHIC | No — needs global view, P1 gate handles gaps |
| 3 | Decision decomposition retry (P1 gate) | MONOLITHIC | Same |
| 4-15 | Decision resolution (N decisions) | DECOMPOSED | Already done — strongest part |
| 16 | Contradiction check (P3) | ATOMIC | No — needs global view |
| 17+ | Contradiction re-resolution | DECOMPOSED | Already done |
| 18 | **Synthesis reasoning** | **MONOLITHIC** | **NO — tested, regressed** |
| 19 | Self-critique | MONOLITHIC | Low priority, skip |
| 20-31 | Field extractions (12 groups) | DECOMPOSED | Already done |
| 32 | **Artifact building (tool loop)** | **MONOLITHIC** | **NO — tested, regressed** |
| 33 | Artifact template fallback | DECOMPOSED | Working, keep |
| 34 | Artifact retry | DECOMPOSED | Working, keep |
| 35 | Grounding validation (LLM) | MONOLITHIC | Keep |
| 36 | Grounding repair (per artifact) | DECOMPOSED | Marginal, keep for now |
| 37 | Coherence check | MONOLITHIC | No — already compact |

## Remaining Levers (Not Yet Tested)

The floor problem (33-36 on baseline, 28 on worst) is driven by:
1. **Decision-level architectural misreads** — model takes shortcut architectures
2. **Method/attribute fabrication in artifacts** — model invents plausible-sounding names

Neither P0 nor P1 addressed these because:
- P0 tried to catch fabrication at output time (too late, model can't fix)
- P1 tried to reduce fabrication by focusing context (broke coherence)

Possible next directions:
1. **Model upgrade**: 40B MoE at Q5 may simply lack the capacity. Testing with 70B+ or a different architecture might help.
2. **Better structural index in resolution prompts**: Resolutions only see 1-3 files. If we could get the target file's real `__init__` attributes into every resolution (not just the call graph segment), the evidence would be more grounded. This is upstream of synthesis.
3. **Template improvements**: The template-constrained approach (auto-extracting attrs from `__init__`) in run 15 helped (+2.2 mean). There may be room for more targeted template engineering.
4. **Prompt engineering in synthesis**: The "trace the call chain" instruction (run 35) raised the floor from 33 to 37. More targeted instructions might help further.
