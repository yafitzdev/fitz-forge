# Scorer V2 — Benchmark Tracker

**Task:** Add query result streaming so answers are delivered token-by-token instead of waiting for the full response
**Target codebase:** fitz-sage
**Model:** qwen3-coder-next-reap-40b-a3b-i1 (Q5_K_S, 65K context)
**Scorer:** V2 deterministic (0-100). Source-augmented index. Regex fab fallback. Cascade-safe consistency.

---

## Scoring Formula

```
completeness (0-30) + artifact_quality (0-50) + consistency (0-20) = deterministic (0-100)
```

- **Completeness**: required files from taxonomy (engine.py, routes/query.py = required; synthesizer.py = recommended; schemas, sdk, services = optional)
- **Artifact quality**: size-weighted mean of per-artifact scores. Each artifact scored on: parseable (10%), fabrication (50%, combined count scaled), hygiene (20%), streaming behavior (20%). Fabrication detected via AST when parseable, regex fallback when not.
- **Consistency**: cross-artifact method name agreement, type agreement, no duplicates. Unparseable artifacts excluded as targets to prevent cascade penalties.

Zero LLM cost. Same plan always gets the same score. Source-dir augmentation validates against full codebase with method merging (not just retrieval subset).

---

## Scored Runs

| Run | Date | Config | Plans | Avg | Range | Fab | Parse Fail | Dupes | Notes |
|-----|------|--------|-------|-----|-------|-----|------------|-------|-------|
| 81 | 04-07 | Baseline (surgical engine, per-func routes) | 5 | 77.6 | 67-87 | 8 | 3 | 2 | V2 baseline. |
| 82 | 04-07 | + class cache (reverted) | 10 | 75.3 | 68-92 | 18 | 19 | 4 | Class cache neutral-to-negative. Reverted. |
| 83 | 04-07 | + surgical synthesizer.py | 10 | 86.5 | 76-100 | 1 | 11 | 6 | First 100/100 plan. Surgical synth regex fix. |
| 84 | 04-08 | + artifact dedup (V2-F5 fix) | 7 | 88.3 | 75-98 | 14 | 6 | 0 | V2-F5 fixed. 14 fabs = real invented classes (F8a-c). |
| 85 | 04-08 | + F8a raw-string constraint (reverted) | 9 | 72.6 | 20-86 | 1 | 10 | 0 | Prompt hack caused regression. **Reverted.** |
| 86 | 04-08 | + V2-F7 injection (reverted) | 9 | 80.7 | 66-92 | 4 | 15 | 0 | Injection fires but artifacts fail. **Reverted.** |
| 87 | 04-08 | + decomp scorer (graph_cov gate too strict) | 7 | 79.2 | 65-93 | 9 | 7 | 0 | graph_cov gate impossible to clear. Fixed. |
| 88 | 04-08 | + decomp scorer + consistency cascade fix | 7 | 84.6 | 67-100 | 14 | 11 | 0 | 0 missing files. Fab 14->2 from decomp fix. Consistency cascade eliminated. Parse failures = remaining bottleneck. |
| 89 | 04-09 | + LLM quality layer (generate.py) | 10 | 86.8→**90.0** | 73.8→77.1 — 95→**100** | 8 | 16 | 0 | generate() with budget cap + truncation retry. Scorer: parse recovery + codebase awareness + private method skip. |

### Run progression

| Metric | Run 84 (pre-decomp) | Run 88 (post-decomp) | Run 89 (+ quality layer + scorer fixes) |
|--------|---------------------|---------------------|----------------------------------------|
| Plans | 7 | 7 | 10 |
| Avg | 88.3 | 84.6 | **90.0** |
| Range | 75-98 | 67-100 | **77.1-100.0** |
| Completeness | 26/30 | **30/30** | **30/30** |
| Missing files | 2/7 | **0/7** | **0/10** |
| Fabrications | 14 | 14 | **8** |
| Parse failures | 6 | 11 | 16 |
| Consistency avg | 18.3/20 | 13.5/20 | **15.3/20** |
| 95+ plans | 1/7 | 1/7 | **4/10** |
| 100 plans | 0/7 | 0/7 | **1/10** |

Run 89: LLM quality layer (generate.py) + three scorer fixes (parse recovery in consistency checker, codebase method awareness, private method skip). Also: broader reference method detection in pipeline (affects future plans only).

---

## Current Failure Patterns

| ID | Pattern | Occurrence (run 89) | Measured Impact | Fix Type | Status |
|----|---------|---------------------|-----------------|----------|--------|
| V2-F1 | Engine.py parse failure (truncation) | 1/10 | -3 pts | LLM quality layer reduces but doesn't eliminate | Mitigated |
| V2-F2 | Small artifact parse failure | 6/10 | ~0 pts | — | **Won't fix** (no score impact) |
| V2-F3 | Streaming file missing yield | 0/10 | — | — | Not seen |
| V2-F4 | NotImplementedError stub | 0/10 | — | — | Not seen |
| V2-F5 | Duplicate artifacts | 0/10 | — | Deterministic dedup | **Fixed** (run 84) |
| V2-F6a | Consistency: scorer parse recovery gap | — | — | Parse recovery added to `_extract_method_definitions` | **Fixed** (run 89) |
| V2-F6b | Consistency: calls to existing codebase methods | — | — | Codebase method awareness + private method skip | **Fixed** (run 89) |
| V2-F6c | Consistency: type disagreement (answer_stream→Answer) | 3/10 | -10 pts | Model returns blocking type from streaming method | Open (LLM quality) |
| V2-F6d | Consistency: genuine method name mismatch | 1/10 | -7 pts | `_synthesizer.stream()` vs `generate_stream()` | Open (rare) |
| V2-F7 | Missing required file | 0/10 | — | Decomp scorer + prompt fix | **Fixed** (run 88) |
| V2-F8a | Fabricated methods on tangential files | 1/10 | -13 pts | Broader reference method detection (pipeline fix, future plans) | Mitigated |
| V2-F8b | Fabricated provider subclasses | 0/10 | — | Decomp scorer ref_complete fixes root cause | **Fixed** (run 88) |
| V2-F8c | Fabricated request DTOs | 0/10 | — | Not seen in run 89 | Resolved |

**Current state:** Run 89 (rescored). LLM quality layer + scorer fixes + broader reference method detection.

**Score: avg 90.0/100, range 77.1-100.0, 4/10 plans at 95+, 1 perfect 100.**

**Remaining issues (all LLM quality, not scorer/pipeline bugs):**
1. V2-F6c: model names method `answer_stream` but returns blocking `Answer` type (3/10)
2. V2-F8a: fabrication on tangential files — broader ref method detection should help (future plans)
3. V2-F6d: genuine method name mismatch across artifacts (1/10, rare)

---

## Scorer Changelog

| Date | Change |
|------|--------|
| 04-07 | V2 scorer created: deterministic checks, taxonomy classification, completeness from taxonomy |
| 04-07 | Parse recovery: dedent + class wrap for code fragments |
| 04-07 | Source-dir augmentation: full codebase scan for class/method validation |
| 04-07 | Method merge: augmentation fills missing methods on existing index classes |
| 04-07 | Combined fabrication weight: single 50% bucket instead of 4x12.5% |
| 04-07 | Unparseable fab score: 0.5 (unknown) instead of 0.0 (assumed worst) |
| 04-08 | Regex fabrication fallback: detect fabs on unparseable code via string scan |
| 04-08 | Skip list expanded: TypeVar, callable, reversed, stdlib classes |
| 04-08 | Size-weighted artifact quality: larger artifacts carry more weight |
| 04-08 | Comment stripping in regex scanner (prevents false positives from "# Step 2: Batch(...)") |
| 04-08 | Local def skipping: functions defined + called in same artifact are not fabrications |
| 04-08 | Consistency cascade fix: unparseable artifacts excluded as targets (no double-counting) |
| 04-09 | V2-F6a fixed: parse recovery (dedent/class wrap) added to `_extract_method_definitions` |
| 04-09 | V2-F6b fixed: consistency checker skips methods that exist in codebase structural index |
| 04-09 | V2-F6b fixed: private method calls (starting with `_`) excluded from consistency checks |

## Pipeline Changelog

| Date | Change |
|------|--------|
| 04-07 | Surgical synth: regex fix for "convert X into" pattern |
| 04-07 | Artifact dedup: removes duplicate filenames post-generation (V2-F5) |
| 04-08 | Decomp scorer: ref_complete criterion (15pts) — penalizes missing definition files |
| 04-08 | Per-criterion quality gates: each criterion must clear its minimum, retry up to 4 |
| 04-08 | Decomp prompt: "include the file where the class is DEFINED" |
| 04-08 | graph_cov gate removed (structurally impossible to clear) |
| 04-08 | Results folder restructure: YYYY-MM-DD_HH-MM-SS_run_NNN format |
| 04-09 | LLM quality layer: standalone `generate()` in `fitz_forge/llm/generate.py` — budget cap, sanitization, truncation retry |
| 04-09 | All 36 `client.generate()` call sites migrated to `generate()` |
| 04-09 | Broader reference method detection: matches any method name in purpose text against source file (no verb pattern required, private methods included) |
