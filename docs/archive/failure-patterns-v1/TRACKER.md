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
| 91 | 04-10 | + artifact black box (raw code output) | 6 | 87.3 | 73.3-100.0 | 3 | 1 | 0 | Artifact generation refactored: pluggable strategies, output validation + retry, raw code output (no JSON). Parse fails 16→1. Fabs 8→3. |
| 92 | 04-10 | + fabrication validation dedent recovery | 10 | 92.2 | 75.0-100.0 | 0 | 2 | 0 | Post-run 91 fix. 4/10 perfect 100s. 0 fabrications. Tier 2 taxonomy avg 61.1, combined 76.7. 10/10 routes R3. |
| **93** | **04-10** | **+ artifact closure family (5 invariants)** | **8** | **87.7** | **66.9-99.6** | **0** | **3** | **0** | **Batch-level `generate_artifact_set` with closure + usage + kwargs + imports + field checks. `fitz_service.py` in 6/8 plans (was 0/10 in run 92). **Routes: R1 0→38%, R3 100%→50%**. Consistency 16.0→18.6. But deterministic dropped 4.5pt due to V2-F7 sample variance (3/7 missing engine.py) and plan 04 total reasoning failure. Tier 2 avg 57.0, combined 72.4. New precision limit found: transitive fabrications via untyped locators (e.g. `create_engine()` with no return annotation). See `benchmarks/results/2026-04-10_11-17-46_run_013/SCORE_V2_COMBINED.md`.** |

### Run progression

| Metric | Run 84 | Run 88 | Run 89 | Run 91 | Run 92 |
|--------|--------|--------|--------|--------|--------|
| Plans | 7 | 7 | 10 | 6 | 10 |
| Avg | 88.3 | 84.6 | 90.0 | 87.3 | **92.2** |
| Range | 75-98 | 67-100 | 77.1-100 | 73.3-100 | **75-100** |
| Completeness | 26/30 | 30/30 | 30/30 | 28/30 | 27/30 |
| Fabrications | 14 | 14 | 8 | 3 | **0** |
| Parse failures | 6 | 11 | 16 | **1** | 2 |
| Consistency avg | 18.3/20 | 13.5/20 | 15.3/20 | 16.7/20 | 16.0/20 |
| 100/100 plans | 0/7 | 0/7 | 1/10 | 1/6 | **4/10** |

Run 91: artifact generation black box with raw code output, pluggable strategies (surgical + new_code), output validation (parseable, fabrication, yield, return type), retry with error feedback. Parse failures nearly eliminated. Fabrication validation had a bug (missing dedent recovery in check_artifact) — fixed post-run.

Run 92: first run after the dedent fix in `_check_fabrication`. Zero fabrications across 10 plans confirms the fix works. **4/10 perfect 100s — best run yet.** Remaining gaps: (1) 3/10 missing engine.py (V2-F7, upstream synthesis issue), (2) consistency at 16/20 avg — 5/10 plans at 10/20 from cross-artifact method name mismatches (V2-F6d).

---

## Current Failure Patterns

| ID | Pattern | Occurrence (run 92) | Measured Impact | Fix Type | Status |
|----|---------|---------------------|-----------------|----------|--------|
| V2-F1 | Engine.py parse failure | 0/10 | — | Raw code output eliminates JSON parse issues | **Fixed** (run 91) |
| V2-F2 | Small artifact parse failure | 2/10 | ~0 pts | Raw code output reduces; residual from truncation | Mitigated |
| V2-F3 | Streaming file missing yield | 0/10 | — | Artifact validation checks for yield + retries | **Fixed** (run 91) |
| V2-F4 | NotImplementedError stub | 0/10 | — | Artifact validation detects (soft fail) | Not seen |
| V2-F5 | Duplicate artifacts | 0/10 | — | Deterministic dedup | **Fixed** (run 84) |
| V2-F6a | Consistency: scorer parse recovery gap | — | — | Parse recovery in `_extract_method_definitions` | **Fixed** (run 89) |
| V2-F6b | Consistency: calls to existing codebase methods | — | — | Codebase method awareness + private method skip | **Fixed** (run 89) |
| V2-F6c | Consistency: type disagreement | 0/10 | — | Artifact validation checks return type + retries | Not seen |
| V2-F6d | Consistency: method name mismatch | 5/10 | -10 pts | `_synthesizer.stream()` vs `generate_stream()` | **Main gap** |
| V2-F7 | Missing required file (engine.py) | 3/10 | -15 pts | Decomp scorer + prompt fix; residual from synthesis reasoning | **Main gap** |
| V2-F8a | Fabricated classes (e.g. AnswerChunk) | 0/10 | — | Dedent recovery in `_check_fabrication` | **Fixed** (run 92) |
| V2-F8b | Fabricated provider subclasses | 0/10 | — | Decomp scorer ref_complete | **Fixed** (run 88) |
| V2-F8c | Fabricated request DTOs | 0/10 | — | Not seen | Resolved |

**Current state:** Run 92. 4/10 perfect 100s. Avg 92.2/100.

**Artifact generation:** black box with pluggable strategies, raw code output (no JSON), validation + retry. Fabrication validation now handles indented surgical artifacts via dedent recovery. 0 fabrications in run 92.

**Remaining issues (the two main gaps):**
1. **V2-F7 (3/10 plans):** Missing engine.py from synthesis reasoning `needed_artifacts`. Upstream issue — the model's reasoning pass decides not to include the engine file. Not a code-generation issue.
2. **V2-F6d (5/10 plans):** Cross-artifact method name mismatches. Model picks one name in engine.py (`generate_stream_answer`) and another in routes (`stream_answer`). Signature injection partially helps but doesn't fully solve it. Would need decision resolution to commit to exact method names before synthesis.

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
| 04-09 | Full LLM provenance tracing: JSON traces per generate() call + stage snapshots |
| 04-09 | Stage-level replay: load snapshot, skip completed stages, re-run rest with real LLM |
| 04-10 | Artifact generation black box: `fitz_forge/planning/artifact/` — pluggable strategies, validation + retry |
| 04-10 | Raw code output: artifacts output Python directly (no JSON wrapping). Eliminates quote mangling. 100% success on isolated tests |
| 04-10 | Surgical rewrite is now default for ANY file with a reference method (not gated by 3+ pipeline steps) |
| 04-10 | Fabrication validation dedent recovery: check_artifact uses dedented content for indented surgical artifacts |
| 04-10 | **Artifact closure principle**: batch-level black box entry `generate_artifact_set` enforces the invariant that every cross-file reference must be satisfied by the codebase or a sibling artifact. On violation, expands the set with a repair artifact (e.g. adds `services/fitz_service.py` when a route calls `service.query_stream` that never existed). Replaces the old per-artifact loop in synthesis. Verified on replay: 2 R3 violations → 1 repair iteration → closure reached. See `docs/roadmap/artifact-closure-principle.md`. |
| 04-10 | **Closure family extended to 5 invariants**: existence, usage (async/sync), kwargs, imports, field access. Adds `self._attr` type tracking by parsing target class `__init__` from disk source. New repair strategy 2: regenerate violator with feedback for non-missing violations (usage/kwargs/field). Replay-verified: closure finds 3 violations on plan 08, strategy 1 adds `fitz_service.py` (covering both missing methods), strategy 2 regenerates the route, final state surfaces 1 residual async/sync mismatch as an honest unclosed set. |
