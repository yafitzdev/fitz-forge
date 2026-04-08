# Scorer V2 — Benchmark Tracker

**Task:** Add query result streaming so answers are delivered token-by-token instead of waiting for the full response
**Target codebase:** fitz-sage
**Model:** qwen3-coder-next-reap-40b-a3b-i1 (Q5_K_S, 65K context)
**Scorer:** V2 deterministic (0-100). Source-augmented index. Regex fab fallback.

---

## Scoring Formula

```
completeness (0-30) + artifact_quality (0-50) + consistency (0-20) = deterministic (0-100)
```

- **Completeness**: required files from taxonomy (engine.py, routes/query.py = required; synthesizer.py = recommended; schemas, sdk, services = optional)
- **Artifact quality**: size-weighted mean of per-artifact scores. Each artifact scored on: parseable (10%), fabrication (50%, combined count scaled), hygiene (20%), streaming behavior (20%). Fabrication detected via AST when parseable, regex fallback when not.
- **Consistency**: cross-artifact method name agreement, type agreement, no duplicates

Zero LLM cost. Same plan always gets the same score. Source-dir augmentation validates against full codebase with method merging (not just retrieval subset).

---

## Scored Runs

| Run | Date | Config | Plans | Avg | Range | Fab | Parse Fail | Dupes | Notes |
|-----|------|--------|-------|-----|-------|-----|------------|-------|-------|
| 81 | 04-07 | Baseline (surgical engine, per-func routes) | 5 | 77.6 | 67-87 | 8 | 3 | 2 | V2 baseline. |
| 82 | 04-07 | + class cache (reverted) | 10 | 75.3 | 68-92 | 18 | 19 | 4 | Class cache neutral-to-negative. Reverted. |
| 83 | 04-07 | + surgical synthesizer.py | 10 | 81.9 | 67-100 | 12 | 11 | 6 | First 100/100 plan. Surgical synth regex fix. |
| **84** | **04-08** | **+ artifact dedup (V2-F5 fix)** | **7** | **83.1** | **74-98** | **25** | **6** | **0** | **Dedup eliminates duplicates. Higher fab = better detection (regex fallback).** |

---

## Current Failure Patterns

| ID | Pattern | Occurrence (run 84) | Measured Impact | Fix Type | Status |
|----|---------|---------------------|-----------------|----------|--------|
| V2-F1 | Engine.py parse failure | 3/7 | -6 pts | Pipeline: output format | Open |
| V2-F2 | Small artifact parse failure | 3/7 | ~0 pts | — | **Won't fix** (no score impact) |
| V2-F3 | Streaming file missing yield | 1/7 | -6 pts | Prompt/retry | Open |
| V2-F4 | NotImplementedError stub | 0/7 | — | — | Not seen in run 84 |
| V2-F5 | Duplicate artifacts | 0/7 | — | Deterministic dedup | **Fixed** (run 84) |
| V2-F6 | Cross-artifact method mismatch | 1/7 | -7 pts | F3 signatures | Open |
| V2-F7 | Missing required file | 2/7 | -7 pts | Completeness check | Open |
| V2-F8 | Fabricated types/methods | 5/7 | TBD | TBD | **NEW — under investigation** |

**Priority:** V2-F8 (fabrication, 5/7) > V2-F7 (missing files, 2/7) > V2-F1 (parse, 3/7) > V2-F6 (mismatch, 1/7)

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
