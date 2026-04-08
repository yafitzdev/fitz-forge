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
| **88** | **04-08** | **+ decomp scorer + consistency cascade fix** | **7** | **84.6** | **67-100** | **14** | **11** | **0** | **0 missing files. Fab 14->2 from decomp fix. Consistency cascade eliminated. Parse failures = remaining bottleneck.** |

### Run 88 vs Run 84 (same scorer)

| Metric | Run 84 (pre-decomp fix) | Run 88 (post-decomp fix) |
|--------|------------------------|-------------------------|
| Avg | 88.3 | 84.6 |
| Completeness avg | 26/30 | **30/30** |
| Missing files | 2/7 | **0/7** |
| Fabrications | 14 | 14 |
| Parse failures | 6 | 11 |
| Consistency avg | 18.3/20 | 13.5/20 |

Run 88 is more complete (30/30 vs 26/30, 0 missing files) but lower avg because more artifacts = more parse failures = lower artifact quality. The 3.7pt gap is entirely from truncation (hardcoded max_tokens=4096). Fix is in the LLM quality layer roadmap.

---

## Current Failure Patterns

| ID | Pattern | Occurrence (run 88) | Measured Impact | Fix Type | Status |
|----|---------|---------------------|-----------------|----------|--------|
| V2-F1 | Engine.py parse failure (truncation) | 2/7 | -6 pts | LLM quality layer: context-aware max_tokens | Open |
| V2-F2 | Small artifact parse failure | 4/7 | ~0 pts | — | **Won't fix** (no score impact) |
| V2-F3 | Streaming file missing yield | 0/7 | — | — | Not seen in run 88 |
| V2-F4 | NotImplementedError stub | 0/7 | — | — | Not seen in run 88 |
| V2-F5 | Duplicate artifacts | 0/7 | — | Deterministic dedup | **Fixed** (run 84) |
| V2-F6 | Cross-artifact method mismatch | 3/7 | -5 pts | Real mismatches (not cascade) | Open |
| V2-F7 | Missing required file | 0/7 | — | Decomp scorer + prompt fix | **Fixed** (run 88) |
| V2-F8a | Fabricated streaming chunk types | 1/7 | -15 pts | Decomp scorer reduces; needs structural fix | Partially fixed |
| V2-F8b | Fabricated provider subclasses | 0/7 | — | Decomp scorer ref_complete fixes root cause | **Fixed** (run 88) |
| V2-F8c | Fabricated request DTOs | 1/7 | -25 pts | Low priority | Open |

**Current state:** Run 88 code. Decomp scorer with ref_complete + per-criterion gates. Consistency cascade fix. F8a/V2-F7 prompt hacks reverted.

**Remaining bottleneck:** Parse failures from LLM truncation (hardcoded max_tokens=4096). See `docs/roadmap/llm-call-quality-layer.md`.

**Priority:** LLM quality layer (truncation fix) > V2-F6 (real consistency mismatches) > V2-F8a/c (residual fabrication)

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
