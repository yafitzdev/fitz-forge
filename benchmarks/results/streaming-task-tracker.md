# Benchmark Tracker: Query Result Streaming

**Task:** Add query result streaming so answers are delivered token-by-token instead of waiting for the full response
**Target codebase:** fitz-sage
**Model:** qwen3-coder-next-reap-40b-a3b-i1 (Q5_K_S, 65K context) unless noted otherwise

---

## Sonnet-Scored Runs (1-51)

Scored by Sonnet-as-Judge on 6 dimensions (each 1-10, total /60). High variance (same plan can score 36 vs 48).

| # | Date | Pipeline | Decisions | Time | Files | Contract | Consistency | Alignment | Implement | Scope | **Total** | Notes |
|---|------|----------|-----------|------|-------|----------|-------------|-----------|-----------|-------|-----------|-------|
| 1 | 03-27 | decomposed v4 (30B Q6) | 5 | 189s | 4 | 2 | 4 | 3 | 3 | 4 | **20** | 30B model. Treats streaming as 2-file API change. |
| 2-4 | 03-27 | nemotron-cascade-2-30b | 5-7 | 586-730s | — | — | — | — | — | — | **DNF** | Cascade reasoning uses opaque `<SPECIAL_30>` tokens. Model incompatible. |
| 5 | 03-27 | decomposed v4 (80B IQ3_S) | 15 | 349s | 7 | 8 | 6 | 4 | 4 | 7 | **36** | Big jump from 30B. Correct arch but wrong field names/methods. |
| 6 | 03-28 | decomposed v4 (80B IQ4_XS) | 10 | 3716s | 7 | 7 | 6 | 4 | 4 | 7 | **35** | 10x slower (VRAM spill). Higher quant = no quality gain. |
| 7 | 03-28 | + source injection (27K chars) | 13 | 327s | 6 | 7 | 5 | 4 | 3 | 7 | **32** | REGRESSION. Too much context confused model. |
| 8 | 03-28 | + compact cheat sheet (4K) | 10 | 307s | 7 | 7 | 6 | 5 | 5 | 7 | **37** | Less noise than source dump. |
| 9 | 03-28 | + cheat sheet (40B reap Q5) | 12 | 264s | 7 | 9 | 6 | 5 | 5 | 7 | **39** | NEW BEST. 40B at Q5 = 80B IQ3_S quality but faster. |
| 10-12 | 03-28 | + flows / no line nums / params | 10-15 | 280-318s | 5-7 | 5-8 | 4-5 | 3-5 | 3-5 | 6-7 | **28-35** | Flows HURT. Line nums/params = no effect. |
| 13a-e | 03-28 | variance test (same as 12) | 12-14 | 287-328s | 6.8 | 7.6 | 6.4 | 4.2 | 4.4 | 7.2 | **36.6 avg** | 5 runs: 30-41. Stdev=4.8. |
| 14a-e | 03-28 | baseline no fixes | 11-14 | 260-344s | 6.6 | 7.4 | 5.6 | 4.4 | 4.6 | 7.0 | **35.6 avg** | 5 runs: 33-43. Stdev=4.2. |
| 15a-j | 03-28 | template-constrained attrs | 10-13 | 247-343s | 7.3 | 8.0 | 5.4 | 4.8 | 5.0 | 7.3 | **37.8 avg** | 10 runs: 29-47. TWO plans hit 45+. |
| 16a-j | 03-28 | artifact resolution BROKEN | 11-14 | 331-505s | 6.4 | 6.1 | 4.3 | 4.4 | 4.4 | 6.5 | **32.1 avg** | BUG: 12-21 artifacts. Reverted. |
| 17a-j | 03-28 | artifact resolution BUGFIX | 10-15 | 251-344s | 7.3 | 8.0 | 5.4 | 4.8 | 5.0 | 7.3 | **37.8 avg** | Bug: context not populated. Identical to run 15. |
| 18a-j | 03-28 | artifact resolution FIXED | 12-15 | 270-344s | 7.1 | 7.8 | 4.8 | 3.8 | 4.0 | 7.0 | **34.5 avg** | REGRESSION. More code = more fabrication surface. |
| 19a-j | 03-28 | + component method sigs | 10-13 | 249-295s | 6.8 | 8.0 | 6.4 | 4.4 | 4.8 | 7.1 | **37.5 avg** | No improvement on alignment. Model still invents params. |
| 20a-e | 03-28 | + full-sig evidence | 12-14 | 287-777s | 7.4 | 8.6 | 5.6 | 5.2 | 5.4 | 7.4 | **39.2 avg** | Lowest stdev (3.7). +3.6 vs baseline. |
| 21a-e | 03-29 | + tool-assisted artifacts | 11-13 | 308-1105s | 7.2 | 8.2 | 6.6 | 5.6 | 6.0 | 7.8 | **41.4 avg** | NEW BEST. Tools 2/5 scored 45. |
| 22a-e | 03-29 | + smart exit dedup | 10-15 | 251-303s | 6.8 | 8.4 | 6.2 | 5.6 | 5.2 | 7.2 | **39.6 avg** | Lowest stdev (2.7). Floor 37. |
| 23a-e | 03-29 | + pre-fill class lookups | 10-12 | 293-356s | 7.0 | 7.6 | 5.6 | 4.6 | 4.8 | 7.6 | **37.2 avg** | REGRESSION. Model skipped tools. |
| 24a-e | 03-29 | remove check_exists + max5 | 10-14 | 280-346s | 7.2 | 8.0 | 5.2 | 5.2 | 5.4 | 7.2 | **38.2 avg** | Variable research quality. |
| 25a-e | 03-29 | + tool history + forced exit 2 | 12-14 | 285-346s | 7.6 | 7.2 | 5.8 | 4.4 | 5.0 | 7.4 | **37.4 avg** | Lowest stdev (2.5). 100% tool success. |
| 26a-e | 03-29 | + forced exit 3 rounds | 12-14 | 251-317s | 7.4 | 6.8 | 5.6 | 4.8 | 5.0 | 7.4 | **37.0 avg** | Extra round didn't help. |
| 27 | 03-29 | + silent dedup + max10 | — | — | — | — | — | — | — | — | **DNF** | Infinite duplicate loop. |
| 28a-e | 03-29 | tool-enriched template | 12-14 | 294-332s | 7.6 | 8.4 | 6.6 | 6.0 | 6.8 | 8.0 | **43.4 avg** | **NEW BEST.** Tools gather, template extracts. 46, 48 highest ever. |
| 29a-d | 03-29 | + baseline pre-call | 12-14 | 282-315s | 8.0 | 7.0 | 6.3 | 5.0 | 5.0 | 7.8 | **39.0 avg** | REGRESSION. Pre-fill always hurts. |
| 30a-e | 03-29 | + disk grep + Pydantic fields | 12-14 | 281-359s | 8.0 | 7.0 | 5.6 | 4.8 | 5.0 | 7.6 | **38.0 avg** | REGRESSION. Wrong files found. |
| 31a-d | 03-29 | DIFFERENT TASK (token tracking) | 12 | 294-342s | 6.8 | 6.2 | 4.4 | 4.0 | 4.2 | 6.0 | **30.8 avg** | Not comparable. |
| 32-34 | 03-29 | refactor verification | 12 | 267-356s | — | — | — | — | — | — | **33-42** | Reverted refactor, kept tests. |
| 35a-e | 03-29 | + synthesis prompt fix | 12 | 258-306s | 7.6 | 8.0 | 6.0 | 5.6 | 5.4 | 7.6 | **40.6 avg** | Floor 33->37. |
| 36a-e | 03-30 | + artifact coverage retry | 12 | 284-331s | 8.0 | 8.0 | 6.4 | 6.0 | 5.6 | 7.2 | **43.2 avg** | Four plans at 45-47. |
| 37a-e | 03-30 | + improved retry | 12 | 266-331s | 7.8 | 8.0 | 6.2 | 7.0 | 5.6 | 8.0 | **42.6 avg** | Ceiling 48. Floor still 33. |
| 38a-e | 03-30 | + AST quality gate v1 | 12 | 260-331s | 7.2 | 8.2 | 6.0 | 5.2 | 5.0 | 7.4 | **39.0 avg** | Retry can't fix fabrication. |
| 39a-e | 03-30 | + AST quality gate v2 | 12 | 262-319s | 7.6 | 8.0 | 5.6 | 5.4 | 5.6 | 7.8 | **40.0 avg** | Gate worked but scorer catches deeper issues. Reverted. |
| 40a-e | 03-30 | + P1: coverage gate | 12-13 | — | 8.2 | 7.4 | 6.8 | 5.6 | 5.6 | 8.4 | **42.0 avg** | Floor 36. Ceiling 49. |
| 41a-e | 03-30 | + P2: layer warning | 12-13 | — | 8.4 | 7.6 | 6.6 | 6.6 | 6.6 | 8.4 | **44.2 avg** | Ceiling 53 (record). |
| 42a-e | 03-30 | + P3: contradiction detect (buggy) | 12-13 | — | 7.8 | 8.4 | 6.8 | 6.2 | 6.2 | 8.6 | **44.0 avg** | P3 was no-op (wrong JSON keys). |
| 43a-e | 03-30 | + P3 fixed | 12-14 | — | 8.4 | 7.4 | 6.6 | 6.0 | 6.2 | 8.4 | **43.0 avg** | P3 caught contradictions but floor unchanged. |
| 44a-e | 03-30 | + P4: field grounding | 12-13 | — | 7.8 | 8.4 | 6.8 | 6.2 | 6.8 | 8.0 | **42.0 avg** | P4 said "no corrections" on ALL plans. |
| 45a-e | 03-30 | + P4 v2 + grounding repair | 12-15 | — | 5.8 | 7.4 | 4.6 | 3.8 | 3.8 | 6.4 | **36.4 avg** | Bimodal. Variance confirmed in run 46. |
| 46a-e | 03-30 | + false-positive fix | 12-15 | — | 7.8 | 8.6 | 6.0 | 7.0 | 6.4 | 8.0 | **43.6 avg** | Best floor: 40. |
| 47a-e | 03-30 | P4 removed (dead weight) | 12-14 | — | 7.4 | 8.6 | 5.0 | 5.8 | 5.4 | 7.4 | **39.6 avg** | P4 removal safe. |
| 48a-e | 03-30 | same as 47 (confirmation) | 12-14 | — | 8.0 | 7.5 | 5.8 | 6.8 | 5.5 | 8.0 | **41.5 avg** | 4/5 plans. |
| 49a-e | 03-30 | P4 re-enabled (A/B test) | 12-14 | — | 7.4 | 8.2 | 5.2 | 5.6 | 4.8 | 7.2 | **39.0 avg** | P4 confirmed dead weight. |
| 50a-e | 03-30 | chain call grounding — REVERTED | 12-14 | — | 7.2 | 7.4 | 6.0 | 5.0 | 4.8 | 6.8 | **38.2 avg** | False positives on un-indexed classes. |
| 51a-e | 03-30 | enriched repair — REVERTED | 12-15 | — | 5.6 | 7.4 | 6.2 | 3.4 | 3.8 | 5.6 | **33.2 avg** | LLM picks semantically wrong methods. |

---

## Deterministic-Scored Runs (52+)

Scored by AST-based deterministic scorer (0-100). Zero variance. Measures: fabricated refs, field errors, syntax errors, artifact coverage.

| # | Date | Pipeline | Det-Score | Fab | Field | Syn | Cov | Notes |
|---|------|----------|-----------|-----|-------|-----|-----|-------|
| 52a-j | 03-31 | BASELINE monolithic template | 73.0 | 1.7 | 0.3 | 0.5 | 77% | Low fabrication but low coverage. Writes less code. |
| 53a-j | 03-31 | PER-ARTIFACT + init + schema | 63.5 | 3.5 | 0.2 | 1.1 | 88% | 2x more code, 2x more fabrication. |
| 54a-j | 04-01 | + class interface injection (compressed source bug) | 70.4 | 2.3 | 0.2 | 1.0 | 94% | Interfaces only for small files. Fab -34%. |
| 55a-g | 04-01 | + type-aware repair (no disk fix) | 61.7 | 1.7 | 1.9 | 1.6 | 100% | Zero-fab 43%. Field errors spiked (variance). 7 runs. |
| 56a-j | 04-01 | + DISK SOURCE FIX | 75.5 | 1.0 | 0.6 | 0.9 | 95% | ROOT CAUSE FIX. engine.py: 3780->58251 chars. Best run: 95. |
| 57a-j | 04-01 | + compact synth + uncapped | 74.4 | 2.0 | 0.7 | 0.2 | 94% | Uncapped reasoning hurt attention. Syn nearly eliminated. |
| 58a-j | 04-02 | + compressed reasoning | 72.6 | 1.6 | 0.2 | 1.1 | 96% | 20K->15.5K reasoning. Best run: 93. |
| 59a-j | 04-02 | + sectioned extraction | 76.0 | 1.4 | 0.4 | 0.9 | 96% | NEW BEST. Roadmap/Risk use Design output. Best run: 92. |

---

## Change Log

| Date | Change | Impact |
|------|--------|--------|
| 03-27 | Baseline run (30B Q6) | 20/60 |
| 03-28 | Source injection (27K chars) | HURT (32 vs 36). Too much context. |
| 03-28 | Compact cheat sheet (4K) | Slight help (+1). |
| 03-28 | Template-constrained attrs from __init__ AST | HELPED (+2.2 mean, +4 ceiling). |
| 03-28 | Method flows — NOT wired | HURT when wired. |
| 03-28 | Artifact resolution (per-file LLM calls) | HURT (-3.3). More code = more fabrication. |
| 03-28 | Full-sig evidence + parallel param rule | HELPED (+3.6 vs baseline). |
| 03-29 | Tool-assisted artifact building | NEW BEST (41.4). Tools 2/5 scored 45. |
| 03-29 | Tool-enriched template (tools gather, template extracts) | **BREAKTHROUGH (43.4)**. Best overall approach for Sonnet scoring. |
| 03-29 | Synthesis prompt fix: "trace call chain" | Floor 33->37. |
| 03-30 | Artifact coverage retry | Four plans at 45-47. |
| 03-30 | P1 coverage gate + P2 layer warning + P3 contradiction detect | Ceiling 53. |
| 03-30 | P4 field grounding | Dead weight. Killed. |
| 03-30 | Grounding repair (AST violations -> LLM fix) | Active. Best floor: 40. |
| 03-31 | Per-artifact generation + init preservation + schema injection | Coverage 77->88%. Fabrication 1.7->3.5 (trade-off). |
| 03-31 | Deterministic scorer | Zero-variance. Essential for A/B testing. |
| 04-01 | Class interface injection | Fab 3.5->2.3 (-34%). 5216 chars of grounded data. |
| 04-01 | Type-aware repair + test leak filter | Fab 2.3->1.7. Zero-fab 43%. |
| 04-01 | **DISK SOURCE FIX** | **ROOT CAUSE.** Fab 1.7->1.0 (-71%). Score surpasses baseline. |
| 04-01 | Compact synthesis prompt (-29% tokens) | Synth prompt 62K->44K chars. |
| 04-01 | Remove artificial caps + budget-aware truncation | 32K token limit. Reasoning truncated last. |
| 04-02 | Compressed reasoning for artifacts | 20K->15.5K. Keep arch+design, drop roadmap/risk. |
| 04-02 | Sectioned extraction (roadmap/risk from Design) | Score 72.6->76.0. NEW BEST. |
| 04-02 | Attr-as-function repair | self._assembler() -> self._assembler.assemble(). |

---

## Key Learnings

**What works:**
- Decomposition is the strongest weapon (per-decision, per-field, per-artifact)
- Deterministic repair > LLM repair (LLM picks semantically wrong corrections)
- Less context is better (4K cheat sheet > 27K source dump)
- Tools work when the model chooses what to look up (active > passive)
- Pre-filling context always hurts (model skips verification)
- Init preservation + interface injection + type-aware repair = grounded artifacts
- Budget-aware truncation with priority ordering (decisions > source > interfaces > reasoning)

**What doesn't work:**
- More context (source dumps, method flows, full reasoning)
- LLM self-audit (P4 said "no corrections" on ALL plans)
- Enriched repair (LLM picks wrong methods from suggestion lists)
- Forced tool exits (loses the model's natural "I'm ready" signal)
- Pre-fill of any kind (class lookups, tool history, baseline pre-calls)

**Root causes of remaining fabrication (1.4 avg):**
- Attr-as-function: correct name but wrong calling pattern (self._xxx() vs self._xxx.method())
- Invented helpers: model composes multiple real ops into one fake method
- Hallucinated file paths: services.py vs services/fitz_service.py
- Model capability ceiling at 40B parameters

---

## Session Handoffs

### Session 2026-03-31: Per-Artifact Generation + Deterministic Scorer

Shipped per-artifact generation, init preservation, schema field injection, deterministic scorer, fitz-sage rename. Score went from Sonnet-scored ~43/60 to det-scored 63.5/100 baseline.

### Session 2026-04-01: Interface Injection + Type-Aware Repair

Shipped class interface injection, type-aware deterministic repair, disk source fix (root cause), compact synthesis prompt, reasoning compression, sectioned extraction. **Fabrications 3.5 -> 1.4 (-60%). Score 63.5 -> 76.0 (+12.5 points).**

Root cause: `file_contents` stores pre-compressed source with init bodies stripped. Interface injection was receiving 3780-char compressed source instead of 58K-char full source. Fix: read from disk.

### Critical Files

- `fitz_forge/planning/pipeline/stages/synthesis.py` — per-artifact generation, interface injection, type-aware repair, reasoning compression, sectioned extraction
- `fitz_forge/planning/agent/compressor.py` — init preservation
- `fitz_forge/planning/validation/grounding.py` — AST grounding + StructuralIndexLookup
- `benchmarks/eval_deterministic.py` — deterministic scorer
- `benchmarks/BENCHMARK.md` — how to run benchmarks
- `docs/pipeline-architecture.md` — full pipeline technical reference
- `docs/synthesis-refactor.md` — prompt compaction design doc
