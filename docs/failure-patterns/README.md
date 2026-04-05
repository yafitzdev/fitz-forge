# Pipeline Failure Patterns

Catalog of known failure modes in the planning pipeline, with fix status, test data, and instructions for working on them.

## How to Use This Document

### For understanding the current state
- **LLM Call Vulnerability Map**: Shows every LLM call in the pipeline and whether it's protected. ✅ = secured, 🟡 = partially secured, ❌ = no protection.
- **Cross-Cutting Failures table**: The actionable work list. Each row is a specific failure pattern with its current status.
- **Priority Order**: Work on these in order. Top = highest ROI.

### For fixing a failure
1. Check the **Harness** column — does an isolated test script exist? If ❌, build one first (see Testing Methodology below).
2. Run the harness N times (50 recommended) to get a **Before** baseline.
3. Implement the fix.
4. Run the harness N times again to get an **After** measurement.
5. Update the table: set Isolated Runs, Before, After, and flip Status to ✅.
6. Do NOT run full pipeline benchmarks to validate individual fixes — Sonnet scoring variance (~6 stdev) drowns out individual improvements. Full pipeline benchmarks are only useful after multiple fixes are applied.

### For running full pipeline benchmarks
- Use 1-1-1-7 sequence (see `benchmarks/BENCHMARK.md`)
- Score with Sonnet subagents (one per plan)
- Only do this after several failure patterns are fixed, to measure cumulative impact

### Column definitions (Cross-Cutting Failures table)
| Column | Meaning |
|--------|---------|
| **ID** | Failure pattern identifier (F1-F7). Referenced in individual docs in this folder. |
| **Pattern** | Short name for the failure |
| **Occurrence** | How often this happens in full pipeline plans. Based on observed data (e.g., "3/3 plans" or "~67%"). |
| **Impact** | Estimated Sonnet score points lost when this failure occurs. "est." = estimated from dimension weights. Measured values shown when available. |
| **Fix Type** | Implementation complexity. Deterministic = pure code, 0 LLM cost. Prompt = change prompt text only. LLM retry = costs 1 extra LLM call. Cross-validation = post-generation analysis. |
| **Harness** | ✅ = isolated test script exists in `benchmarks/`. ❌ = needs to be built. |
| **Isolated Runs** | Total number of isolated test runs (before + after). 0 = not yet tested. |
| **Before** | Failure rate before fix, measured in isolation. "?" = not yet measured. |
| **After** | Failure rate after fix, measured in isolation. "—" = fix not yet implemented. |
| **Status** | ✅ = fixed and verified. ❌ = not yet fixed. |

### Existing test harnesses
| Script | Purpose | Usage |
|--------|---------|-------|
| `benchmarks/test_decomp_scorer.py` | Generate N decompositions, score each | `python benchmarks/test_decomp_scorer.py` |
| `benchmarks/test_synth_scorer.py` | Generate N synthesis reasoning candidates, score each | `python benchmarks/test_synth_scorer.py` |
| `benchmarks/test_artifact_gen.py` | Generate N artifacts for a target file, check for fabrications | `python benchmarks/test_artifact_gen.py --runs 50 --trace-dir benchmarks/traces/xxx` |
| `benchmarks/test_f1_dedup.py` | Generate N decompositions, check for duplicate decisions | `python benchmarks/test_f1_dedup.py --runs 50` |
| `benchmarks/test_f6_empty.py` | Generate 1 reasoning, run N extractions per critical group | `python benchmarks/test_f6_empty.py --runs 50` |
| `benchmarks/test_f9_compression.py` | Generate N engine.py artifacts, check internal API fabrication | `python -m benchmarks.test_f9_compression --runs 50` |
| `benchmarks/test_f10_service.py` | Generate N query.py artifacts, check FitzService API fabrication | `python -m benchmarks.test_f10_service --runs 50` |
| `benchmarks/test_f11_wrong_object.py` | Generate N engine.py artifacts, check wrong-object method calls | `python -m benchmarks.test_f11_wrong_object --runs 50` |
| `benchmarks/test_f14_path.py` | Generate N reasonings, extract needed_artifacts, check path resolution | `python -m benchmarks.test_f14_path --runs 50` |

### Key files
| File | Role |
|------|------|
| `fitz_forge/planning/pipeline/stages/decision_decomposition.py` | Decomposition stage (F1) |
| `fitz_forge/planning/pipeline/stages/synthesis.py` | Synthesis + artifact generation (F2, F3, F5, F6, F7) |
| `fitz_forge/planning/pipeline/stages/base.py` | JSON extraction, field group extraction (F6) |
| `fitz_forge/planning/prompts/decision_decomposition.txt` | Decomposition prompt template |
| `fitz_forge/planning/prompts/synthesis.txt` | Synthesis reasoning prompt template |

---

## LLM Call Vulnerability Map

Every LLM call in the pipeline that can or has produced failures:

| # | Stage | LLM Call | What Can Go Wrong | Status |
|---|-------|----------|-------------------|--------|
| 1 | Implementation check | 1 call | JSON parse failure | ✅ non-fatal, pipeline continues |
| 2 | **Decision decomposition** | 2 calls (best-of-2) | Duplicate decisions (F1 ✅), parse failure (F8 ✅), too few decisions | ✅ best-of-2 + scorer + dedup + depends_on coercion |
| 3 | Decision resolution | 1 call per decision | Wrong evidence, hallucinated code refs | ✅ contradiction detection + retry + evidence file validation |
| 4 | **Synthesis reasoning** | 3 calls (best-of-3) | Vague reasoning, scope miscalibration, over-engineering (F13) | ✅ best-of-3 + scope consensus + scorer + citation rules |
| 5 | Self-critique | 1 call | Critique too aggressive (deletes valid content) | ✅ length floor check (>30% of original) |
| 6 | Context extraction | 4 calls | Empty fields (F6 ✅), JSON parse failure | ✅ JSON regex fix + retry on empty |
| 7 | Architecture extraction | 2 calls | Empty approaches (F6 ✅), wrong scope statement | ✅ retry on empty + Pydantic defaults |
| 8 | Design extraction | 3 calls | Empty components/ADRs (F6 ✅), missing integration points | ✅ retry on empty + Pydantic defaults |
| 9 | **Per-artifact generation** | 1 call per artifact | Method fabrication (F7 ✅), wrong request fields (F2 ✅), wrong imports (F5 ✅) | ✅ prompt reorder + field repair + import repair |
| 10 | Roadmap extraction | 1 call | Empty phases (F6 ✅), wrong effort estimates | ✅ retry on empty |
| 11 | Scheduling extraction | 1 call | Phantom phase refs (F4 ✅), wrong critical path | ✅ post-extraction filter removes phantom refs |
| 12 | Risk extraction | 1 call | Wrong phase references in risks (F4 ✅) | ✅ phantom phase filter on affected_phases |
| 13 | Grounding validation (AST) | 0 calls | False positives on valid code | ✅ type-aware repair |
| 14 | Grounding repair (LLM) | 1 call per artifact | Repair makes things worse, JSON parse failure | ✅ only applied if violations decrease |
| 15 | Coherence check | 1 call | Over-correction, scope inflation | ✅ advisory only |
| 16 | Confidence scoring | 1 call | Miscalibrated scores | ✅ informational only |

---

## Cross-Cutting Failures

| ID | Pattern | Occurrence | Impact | Fix Type | Harness | Isolated Runs | Before | After | Status |
|----|---------|-----------|--------|----------|---------|---------------|--------|-------|--------|
| F1 | Duplicate decisions | ~17% of raw LLM output | est. ~3 pts | Deterministic dedup | ✅ | 100 (2×50) | 17% (8/47) | **0%** (dedup in execute) | ✅ |
| F2 | Wrong request fields | was 40% of engine.py artifacts | est. ~4 pts | Prompt reorder (F7) | ✅ | 100 (2×50 traces) | 40% (20/50) | **0%** (0/50) | ✅ |
| F3 | Cross-artifact mismatch | ~33% of plans | est. ~5 pts | Signature injection | ❌ | 0 | ? | — (needs full pipeline test) | ✅ |
| F4 | Phantom phases | ~100% of plans | est. ~2 pts | Deterministic filter | ❌ | 0 | ~100% | **0%** (deterministic filter) | ✅ |
| F5 | Wrong imports | ~33% of plans | est. ~2 pts | Index lookup | ❌ | 0 | ? | **0%** (deterministic repair) | ✅ |
| F6 | Empty extraction | was ~10%, now ~0% | est. ~6 pts | LLM retry | ✅ | 150 (3×50) | 0% (0/150) | **0%** (safety net retry) | ✅ |
| F7 | Artifact fabrication | was ~62% | **isolated: 62%→2%. full pipeline: 0 pts** (other failures dominate) | Prompt reorder | ✅ | 100 (2×50) | 62% fail | **2% fail** | ✅ |
| F8 | depends_on int coercion | 6% of decomps | est. ~1 pt (parse failure) | Pydantic validator | ✅ | 100 (2×50) | 6% (3/50) | **0%** (0/50) | ✅ |
| F9 | Source compression blindness | 100% of large-file artifacts | ~10 pts (alignment+implementability) | Ref injection + param fields + callable | ✅ | 200 (4×50) | stubs (4% fab) | **0% fab, 13K real impls** | ✅ |
| F10 | Service API fabrication | 22% plans, 6% artifacts (run 72) | ~8 pts (floor plan driver) | Deterministic corrector + import fix | ✅ | 600 harness + 19 pipeline | 54% plans (run 68) | **22% plans, 11% harness** (run 72) | 🟡 |
| F11 | Wrong object for correct method | 20% of plans (2/10) | ~2 pts | Upstream fix (F9 ref injection) | ✅ | 50 | 0% (0/50) | **0%** (F9 prevents) | ✅ |
| F12 | Artifact filename corruption | 20% of plans (2/10) | ~10 pts (kills file accuracy) | Deterministic cleanup | ❌ | 0 | 20% | **0%** (deterministic) | ✅ |
| F13 | Upstream reasoning failures | 30% of plans (3/10) | ~10 pts (floor plan driver) | Best-of-3 scope consensus | ❌ | 0 | 30% (run 64) | **floor 37 (run 67)** | 🟡 |
| F14 | Wrong service file path | 10% of plans (1/10) | ~8 pts (no source loaded) | N/A (not reproducible) | ✅ | 35 | 0% (0/35) | **0%** (not reproducible) | ✅ |
| F15 | Overfitted decomp examples | 100% of prompts | improved quality | Generic examples | ✅ | 100 (2×50) | 22% dupes | **6% dupes** | ✅ |
| F16 | Overfitted resolution params | 100% of prompts | no impact | Generic examples | ✅ | 100 (2×50) | 0% fab | **0% fab** | ✅ |
| F17 | Overfitted synthesis examples | 100% of prompts | no impact | Generic examples | ✅ | 100 (2×50) | 0% fab | **0% fab** | ✅ |
| F18 | Overfitted artifact rules | 100% of prompts | no impact | Remove example | ✅ | 100 (2×50) | 0% fab | **0% fab** | ✅ |
| F19 | Hardcoded schema keywords | 100% of code paths | load-bearing | N/A (reverted) | ✅ | 50 | 0% fab | **72% fab** (reverted) | ⏸️ |
| F20 | Decision redundancy | 100% of plans | ~3.5K wasted chars (19% of decisions) | File-overlap merger | ✅ | 10 | 13.2 dec, 18.8K | **8 dec, 15.2K** (-19%) | ✅ |

**Fix Types:** Deterministic = pure code, 0 LLM cost. Prompt = change prompt text. LLM retry = extra LLM call. Cross-validation = post-generation check.

**Key insight: more LLM calls + pick the best = proactive fix for model quality limits.** Instead of post-processing bad output, generate multiple candidates and let the scorer filter. Best-of-3 with scope consensus was the single biggest score improvement (+2.8 pts, run 66→67). This principle applies at every stage — the model WILL produce good output some percentage of the time; the job is to select it.

**Current state (run 72):** F10 reduced 54%→22% plan-level via deterministic corrector (AST + regex fallback, chained attrs, underscore strip). Import graph fixed (relative imports). LLM correction prompts all failed. Decision merger (F20) reduces decisions 13→8. All prompts genericized (F15-F18). F19 deferred (load-bearing).

**Key lessons:**
1. More LLM calls + pick the best = proactive fix for model quality limits (best-of-3, +2.8 pts)
2. Frozen-state harness testing misses upstream variance — harnesses must vary ALL stages
3. Prompt instructions can't survive 11K tokens of generation — reduce context instead of adding rules
4. Explore-then-focus: let the model think broadly first, then refine with focused input

---

## Priority Order for Fixes

1. ~~**F4** — Phantom phases.~~ ✅ DONE. Deterministic filter.
2. ~~**F1** — Duplicate decisions.~~ ✅ DONE. String similarity dedup (17%→0%).
3. ~~**F6** — Empty extraction.~~ ✅ DONE. Retry safety net (baseline already 0% after JSON regex fix).
4. ~~**F2** — Wrong request fields.~~ ✅ DONE. Fixed by F7 prompt reorder (40%→0% in traces).
5. ~~**F5** — Wrong imports.~~ ✅ DONE. Deterministic import path repair from structural index.
6. ~~**F3** — Cross-artifact mismatch.~~ ✅ DONE. Prior artifact signature injection (zero LLM cost).
7. ~~**F9** — Source compression blindness.~~ ✅ DONE. Reference method body + param type fields + callable annotation.
8. **F10** — Service API fabrication. 🟡 PARTIALLY. Deterministic corrector (54%→22% pipeline, 11% harness). Import graph fixed. LLM correction failed.
9. ~~**F11** — Wrong object for correct method.~~ ✅ RESOLVED. 0% in isolation — F9 reference injection prevents.
10. ~~**F12** — Artifact filename corruption.~~ ✅ DONE. Deterministic strip of method suffixes.
11. ~~**F13** — Upstream reasoning failures.~~ 🟡 PARTIALLY. Best-of-3 scope consensus raised floor 29→37.

---

## Benchmark History (for this failure pattern work)

| Run | Date | Config | Plans | Avg Score | Notes |
|-----|------|--------|-------|-----------|-------|
| 60 | 2026-04-03 | best-of-2 + prompt eng (pre-reorder) | 10 | 40.1/60 | Baseline for failure analysis |
| 61 | 2026-04-03 | + prompt reorder (F7 fix) | 3 | 37.0/60 | F7 fixed but other failures dominate. Not a regression — within noise. |
| 62 | 2026-04-03 | + F1-F8 all fixed | 3 | 40.3/60 | Flat vs baseline. Structural metrics improved (phase consistency 100%, fab down). F9 identified as bottleneck — source compression removes method bodies, model fabricates internal API calls. |
| 63 | 2026-04-03 | + F9 fixed + bandaids removed | 10 | 40.6/60 | First 3 averaged 46.7 (two 50s!), but 10-plan avg is 40.6. High variance (32-50). Engine.py fab=0 across all 10. Ceiling raised but floor unchanged — non-engine artifacts (SDK, service) still fabricate. |
| 64 | 2026-04-03 | + F10 + F12 + prompt reorder | 10 | 40.3/60 | Range 29-49. Floor plans caused by upstream reasoning (F13): empty architecture, codebase misreads, decision duplication. |
| 65 | 2026-04-03 | + F12 active + F13C pending | 10 | 42.7/60 | +2.6 over baseline. New high: 52/60. Top 5 avg 47.6. Floor 33. |
| 66 | 2026-04-03 | + F13C approach fallback | 10 | 42.5/60 | New high: 53/60. Zero empty architecture sections (F13C working). Floor 33. |
| **67** | **2026-04-04** | **+ best-of-3 scope consensus** | **10** | **45.3/60** | **+5.2 over baseline. Floor 37, two 53s. Top 5 avg 49.2. Scope consensus filtering out over-engineered candidates.** |
| **68** | **2026-04-04** | **+ F10 compose rule + F15-F18 genericize** | **48** | **not scored** | 100% structural success. 46% clean (0 fab). 54% have F10 fab in route/SDK artifacts. Fab originates in synthesis reasoning, not artifact gen. Compose rule works at artifact level (0/50 isolated) but reasoning overrides it. Harness methodology flaw discovered: frozen-state testing misses upstream variance. 1 plan with 0 roadmap phases. |
| 69 | 2026-04-04 | + reasoning split + decision merger + refinement pass | 10 | not scored | 40% clean, 60% F10. Refinement pass fires (31K→6-12K) but F10 persists. |
| 70 | 2026-04-05 | + decision filter + generic fabrication detection | 10 | not scored | 50% plans with F10, 18% artifacts. query.py is persistent hotspot. Decision filter catches fabricated method refs in decisions. Signature filter prevents cross-artifact propagation. Generic: no hardcoded patterns. |
| **72** | **2026-04-05** | **+ deterministic corrector (AST+regex, chained attrs, underscore strip)** | **10** | **40.3/60** | **F10 22% plan-level (0% executable code). Score flat vs baseline (40.1) despite F10 fix. F10 was NOT the score bottleneck — `_detection_orchestrator()` callable (50%) and `_embedder.embed([])` (40%) in engine.py dominate consistency (5.5) and implementability (5.5).** |
