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
| 83 | 04-07 | + surgical synthesizer.py | 10 | 86.5 | 76-100 | 1 | 11 | 6 | First 100/100 plan. Surgical synth regex fix. |
| 84 | 04-08 | + artifact dedup (V2-F5 fix) | 7 | 88.3 | 75-98 | 14 | 6 | 0 | V2-F5 fixed. 14 fabs = real invented classes (F8a-c). |
| 85 | 04-08 | + F8a raw-string constraint (reverted) | 9 | 72.6 | 20-86 | 1 | 10 | 0 | Fab 14->1, but 4/9 missing engine.py. Prompt hack caused regression. **Reverted.** |
| 86 | 04-08 | + V2-F7 injection (reverted) | 9 | 80.7 | 66-92 | 4 | 15 | 0 | Injection fires but artifacts fail. Still missing files. **Reverted.** |
| 87 | 04-08 | + decomp scorer (graph_cov gate too strict) | 7 | 79.2 | 65-93 | 9 | 7 | 0 | graph_cov gate impossible to clear, burned 4 candidates/plan for nothing. |
| **88** | **04-08** | **+ decomp scorer (fixed gates)** | **7** | **82.9** | **67-100** | **2** | **11** | **0** | **0 missing files. Fab 14->2. Another 100/100. Parse failures = remaining bottleneck.** |

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
| V2-F8a | Fabricated streaming chunk types | 3/7 | -15 to -50 pts | Prompt fix reverted — caused engine.py regression | Open |
| V2-F8b | Fabricated provider subclasses | 1/7 | -25 pts | Decision: check existing provider methods | **NEW** |
| V2-F8c | Fabricated request DTOs | 1/7 | -25 pts | Low priority | **NEW** |

**Current state: run 84 code + decomp scorer improvements. F8a/V2-F7 prompt hacks reverted.**

**Structural fixes applied (no prompt hacks):**
- Decomposition scorer: ref_complete criterion (15pts) — penalizes when mentioned classes' definition files are missing from relevant_files
- Per-criterion quality gates — each criterion must clear its minimum independently, retry up to 4 candidates
- Decomposition prompt: "include the file where the class is DEFINED" (generic, not task-specific)

**Lesson: prompt hacks on 30B models are unpredictable. Scorer/selection improvements are safe. Fix the selection mechanism, not the output.**

**Priority:** V2-F8a (fabrication, needs structural fix) > V2-F1 (parse) > V2-F6 (mismatch)

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
