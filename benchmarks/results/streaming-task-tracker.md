# Benchmark Tracker: Query Result Streaming

**Task:** Add query result streaming so answers are delivered token-by-token instead of waiting for the full response
**Target codebase:** fitz-sage
**Scoring:** Sonnet-as-Judge, 6 dimensions x 10 = 60 max

---

## Run Log

| # | Date | Model | Quant | Ctx | Pipeline SHA | Codebase SHA | Pipeline | Decisions | Time | Files | Contract | Consistency | Alignment | Implement | Scope | **Total** | Notes |
|---|------|-------|-------|-----|-------------|-------------|----------|-----------|------|-------|----------|-------------|-----------|-----------|-------|-----------|-------|
| 1 | 2026-03-27 | qwen3-coder-30b-a3b | Q6 | 65K | `65977d5` | `81b5abf` | decomposed v4 | 5 | 189s | 4 | 2 | 4 | 3 | 3 | 4 | **20/60** | Treats streaming as 2-file API change. Misses entire engine→synthesizer pipeline. Artifacts destructively rewrite schemas.py and query.py with stubs. |
| 2 | 2026-03-27 | nemotron-cascade-2-30b-a3b | i1 | 65K | `65977d5` | `81b5abf` | decomposed v4 | 7 | 661s | — | — | — | — | — | — | **DNF** | enable_thinking:false — 0 chars on all resolution/synthesis. Decomposition worked (7 decisions). |
| 3 | 2026-03-27 | nemotron-cascade-2-30b-a3b | i1 | 65K | `65977d5` | `81b5abf` | decomposed v4 | 5 | 586s | — | — | — | — | — | — | **DNF** | enable_thinking removed — still 0 chars. LM Studio streams output to reasoning_content, not delta.content. |
| 4 | 2026-03-27 | nemotron-cascade-2-30b-a3b | i1 | 65K | `65977d5` | `81b5abf` | decomposed v4 | 7 | 730s | — | — | — | — | — | — | **DNF** | reasoning_content captured but contains `<SPECIAL_30>` tokens (49K chars each call). Cascade reasoning mechanism uses opaque tokens, not text. Model incompatible with OpenAI-compat API. |
| 5 | 2026-03-27 | qwen3-coder-next (80B) | IQ3_S | 65K | `65977d5` | `81b5abf` | decomposed v4 | 15 | 349s | 7 | 8 | 6 | 4 | 4 | 7 | **36/60** | Big jump from 30B (20→36). Correct architecture (parallel methods, new endpoint, governance-first). Finds all core files. But artifacts have wrong field names, nonexistent methods, wrong signatures. High-level reasoning good, low-level codebase details wrong. |
| 6 | 2026-03-28 | qwen3-coder-next (80B) | IQ4_XS | 65K | `65977d5` | `81b5abf` | decomposed v4 | 10 | 3716s | 7 | 7 | 6 | 4 | 4 | 7 | **35/60** | Nearly identical to IQ3_S (36→35) but 10x slower (349s→3716s). Same failure pattern: good architecture, hallucinated methods/fields. VRAM spill to system RAM killed performance (~5-10 tok/s vs 80-130). Higher quant = no quality gain at this model size. |
| 7 | 2026-03-28 | qwen3-coder-next (80B) | IQ3_S | 65K | `65977d5` | `81b5abf` | decomposed v4 + source injection + grounding | 13 | 327s | 6 | 7 | 5 | 4 | 3 | 7 | **32/60** | REGRESSION from run 5 (36→32). Source code injection backfired — 27K chars of source confused model. Grounding validator worked (4 AST + LLM gaps). |
| 8 | 2026-03-28 | qwen3-coder-next (80B) | IQ3_S | 65K | `65977d5` | `81b5abf` | decomposed v4 + compact cheat sheet + grounding | 10 | 307s | 7 | 7 | 6 | 5 | 5 | 7 | **37/60** | Best score yet. Compact 4K cheat sheet (class/method names only) improved alignment 4→5 and implementability 4→5 vs baseline. Still hallucinates some methods but fewer. |
| # | Date | Model | Quant | Ctx | Pipeline SHA | Codebase SHA | Pipeline | Decisions | Time | Files | Contract | Consistency | Alignment | Implement | Scope | **Total** | Notes |
| 9 | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | decomposed v4 + cheat sheet + grounding | 12 | 264s | 7 | 9 | 6 | 5 | 5 | 7 | **39/60** | NEW BEST. Contract preservation 9/10 — accurately references all real signatures. Fastest 80B-class run (264s). Misses engine orchestration layer. generate_stream() signature wrong. Reaped 40B at Q5 ≈ 80B IQ3_S quality but faster. |
| 10 | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | decomposed v4 + cheat sheet + grounding + flows in index | 15 | 302s | 5 | 7 | 4 | 4 | 4 | 6 | **30/60** | REGRESSION. Flows in structural index → decomposition blowup (15 decisions, ~7 unique). |
| 11 | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | decomposed v4 + cheat sheet + grounding + flows in cheat sheet | 13 | 318s | 7 | 5 | 4 | 3 | 3 | 6 | **28/60** | REGRESSION. Flows in cheat sheet only — decomposition fine (13 decisions) but artifacts worse. Governance timing wrong. Breaking QueryRequest change. Variance or flows actively confuse the model. |
| 12 | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | decomposed v4 + cheat sheet + grounding + no line nums + method params | 14 | 280s | 6 | 8 | 5 | 4 | 5 | 7 | **35/60** | No-line-numbers fix didn't prevent governance timing error. Method params in index preserved contract (8). get_fitz() still fabricated. High variance between runs (28-39 range). |
| # | Date | Model | Quant | Ctx | Pipeline SHA | Codebase SHA | Pipeline | Decisions | Time | Files | Contract | Consistency | Alignment | Implement | Scope | **Total** | Notes |
| 13a-e | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | same as run 12 (5-run variance test) | 12-14 | 287-328s | 6.8 | 7.6 | 6.4 | 4.2 | 4.4 | 7.2 | **36.6 avg (30-41)** | 5 runs: 30, 39, 41, 33, 40. Stdev=4.8. Median=39. Contract consistently strong (5-9, avg 7.6). Codebase alignment consistently weak (3-5, avg 4.2). Run 9's 39 was NOT an outlier — it's near the median. |
| 14a-e | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | baseline no fixes (5-run variance) | 11-14 | 260-344s | 6.6 | 7.4 | 5.6 | 4.4 | 4.6 | 7.0 | **35.6 avg (33-43)** | 5 runs: 34, 33, 43, 33, 35. Stdev=4.2. Baseline without any fixes. |
| 15a-j | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | template-constrained attrs (10-run) | 10-13 | 247-343s | 7.3 | 8.0 | 5.4 | 4.8 | 5.0 | 7.3 | **37.8 avg (29-47)** | 10 runs. TWO plans hit 45+ (47, 45). Alignment 4.8 vs baseline 4.4 (+0.4). Implementability 5.0 vs 4.6 (+0.4). Contract 8.0 vs 7.4 (+0.6). Higher variance (stdev 5.7) but higher ceiling. |
| # | Date | Model | Quant | Ctx | Pipeline SHA | Codebase SHA | Pipeline | Decisions | Time | Files | Contract | Consistency | Alignment | Implement | Scope | **Total** | Notes |
| 16a-j | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | artifact resolution BROKEN (12-21 arts) | 11-14 | 331-505s | 6.4 | 6.1 | 4.3 | 4.4 | 4.4 | 6.5 | **32.1 avg (25-48)** | 10 runs. BUG: _infer_needed_artifacts fell back to all evidence files → 12-21 artifacts per plan, rewriting entire codebase. Artifacts contradicted decisions. One outlier at 48 (plan 6 with alignment 8). Mostly worse — artifacts too long, too many files, more fabrication surface. |
| 17a-j | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | artifact resolution BUGFIX (context not in prior_outputs) | 10-15 | 251-344s | 7.3 | 8.0 | 5.4 | 4.8 | 5.0 | 7.3 | **37.8 avg (29-47)** | 10 runs. BUG: prior_outputs["context"] not populated before resolve_artifacts(). All 10 runs fell through to template-constrained fallback. Results identical to run 15. Bug not artifact resolution — was never tested. |
| 18a-j | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | artifact resolution FIXED (3-5 arts, context injected) | 12-15 | 270-344s | 7.1 | 7.8 | 4.8 | 3.8 | 4.0 | 7.0 | **34.5 avg (30-41)** | 10 runs. Artifact resolution finally working (3-5 artifacts). REGRESSION vs template (37.8→34.5). Alignment DROPPED 4.8→3.8. More detailed code = more surface area for fabrication. Model writes longer engine artifacts with real-looking but wrong method calls. Lowest stdev (3.7) but lowest mean. |
| 19a-j | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `65977d5` | `81b5abf` | template L2: attrs + component method sigs from source | 10-13 | 249-295s | 6.8 | 8.0 | 6.4 | 4.4 | 4.8 | 7.1 | **37.5 avg (28-46)** | 10 runs. Method sigs on attrs (# has: retrieve(), assemble(query, results) -> str). NO improvement on alignment (4.4 = same as baseline). Consistency improved 5.4→6.4. Contract held at 8.0. The extra method info didn't reduce fabrication — model still invents wrong params. |
| 20a-e | 2026-03-28 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `53c4fe4` | `81b5abf` | full-sig evidence + parallel param rule | 12-14 | 287-777s | 7.4 | 8.6 | 5.6 | 5.2 | 5.4 | 7.4 | **39.2 avg (34-44)** | 5 runs: 44, 41, 34, 38, 39. Stdev=3.7 (lowest). Mean +3.6 vs baseline. generate_stream() now mirrors generate()'s full 6-param signature. Resolution evidence no longer abbreviates with "...". Parallel method param rule enforced. |
| 21a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `37e70d4` | `81b5abf` | tool-assisted artifact building (4 tools) | 11-13 | 308-1105s | 7.2 | 8.2 | 6.6 | 5.6 | 6.0 | 7.8 | **41.4 avg (36-45)** | NEW BEST. 5 runs: 45, 36, 45, 39, 42. Tools succeeded 2/5 (both scored 45). Fallback 3/5 (mean 39). Tool-assisted plans: 0 AST violations, consistency 9/9. Alignment 5.6 (+1.2 vs baseline), implementability 6.0 (+1.4 vs baseline). |
| 22a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `df332c1` | `81b5abf` | tool v2: smart exit (dedup + no-new-info) | 10-15 | 251-303s | 6.8 | 8.4 | 6.2 | 5.6 | 5.2 | 7.2 | **39.6 avg (37-43)** | 5 runs: 37, 42, 43, 38, 38. Lowest stdev ever (2.7). Tools 2/5, smart exit 1/5, fallback 2/5. Tool/fallback scored same (40 each). Dedup caught duplicates. Floor rose 36→37 but ceiling dropped 45→43. |
| 23a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `fb30fc93` | `81b5abf` | pre-fill class lookups in prompt (7/26 classes) | 10-12 | 293-356s | 7.0 | 7.6 | 5.6 | 4.6 | 4.8 | 7.6 | **37.2 avg (31-46)** | REGRESSION. Pre-fill injected 2620 chars of class info into prompt → model skipped tools entirely (0 rounds, 0 calls). Wrote artifacts in 15.8s but quality dropped. Stdev 6.1 (worst). Pre-fill solved wrong problem: warm-up rounds WERE the verification. 5 runs: 31, 40, 38, 46, 31. |
| 24a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `fb30fc93` | `81b5abf` | remove check_exists + max_rounds=5 (3 tools) | 10-14 | 280-346s | 7.2 | 8.0 | 5.2 | 5.2 | 5.4 | 7.2 | **38.2 avg (33-44)** | No check_exists spam. ALL 5 runs exhausted 5 rounds (never produces JSON voluntarily). Runs 1-2 (43-44) did useful research. Runs 3-5 (33-36) wasted calls on framework classes (APIRouter, FastAPI) or fully-qualified paths. Quality depends on WHICH classes model looks up, not whether it uses tools. |
| 25a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `fb30fc93` | `81b5abf` | tool history pre-fill + module strip + forced exit after 2 rounds | 12-14 | 285-346s | 7.6 | 7.2 | 5.8 | 4.4 | 5.0 | 7.4 | **37.4 avg (35-41)** | Lowest stdev EVER (2.5). 100% tool success (all 5 forced after 2 rounds). Tools reliably call right methods now. BUT alignment stuck at 4-5 — model fabricates import paths, field names, helper methods in artifact BODY despite tools verifying class/method signatures. Tool reliability solved but doesn't fix code body fabrication. 5 runs: 35, 39, 41, 35, 37. |
| 26a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `fb30fc93` | `81b5abf` | same as 25 but forced exit after 3 rounds (not 2) | 12-14 | 251-317s | 7.4 | 6.8 | 5.6 | 4.8 | 5.0 | 7.4 | **37.0 avg (33-41)** | Extra round didn't help. Model read_method_source in 3/5 runs but source code HURTS more than helps (run 2 scored 33 despite reading source). Alignment stuck at 3-7, avg 4.8. 0 AST violations in runs 2,5 but scored only 33,37. AST violations ≠ scorer scores. 5 runs: 40, 33, 34, 41, 37. |
| 27 | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `e17c64e9` | `81b5abf` | pre-fill + no forced exit + max_rounds=10 (silent dedup) | — | — | — | — | — | — | — | — | **DNF** | Removed snarky dedup message, kept pre-fill as tool history. Model went into infinite duplicate loop — called pre-filled classes forever since silent dedup gave no signal to stop. 0/4 diagnostic runs produced JSON. Confirmed: model NEVER produces JSON voluntarily in tool mode regardless of config. |
| 28a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `e17c64e9` | `81b5abf` | tool-enriched template (tools gather → template extracts) | 12-14 | 294-332s | 7.6 | 8.4 | 6.6 | 6.0 | 6.8 | 8.0 | **43.4 avg (39-48)** | **NEW BEST (+2.0 vs run 21).** Tools gather verified class/method info (3-9 calls, ~3K chars), then template extraction uses enriched context. No forced exit — early stale detection → template fallback with tool results injected. Plans 4,5 scored 46,48 (highest ever). Alignment 6.0 (+1.2 vs 21), implementability 6.8 (+0.8). 5 runs: 43, 39, 41, 46, 48. |
| 29a-d | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `4ed3b16d` | `81b5abf` | run 28 + baseline pre-call (5/23 classes from resolutions) | 12-14 | 282-315s | 8.0 | 7.0 | 6.3 | 5.0 | 5.0 | 7.8 | **39.0 avg (31-45)** | REGRESSION. Baseline pre-call seeds dedup cache → model's organic lookups flagged as duplicates → earlier stale exit → less research. Pre-filling ALWAYS hurts (runs 23, 25, 27, 29). Run 5 DNF (Pydantic error). 4 runs: 39, 45, 31, 41. |
| 30a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `707b13e8` | `81b5abf` | run 28 + disk grep pass 2 + Pydantic field extraction | 12-14 | 281-359s | 8.0 | 7.0 | 5.6 | 4.8 | 5.0 | 7.6 | **38.0 avg (35-41)** | REGRESSION. Added full-codebase grep for class defs + Pydantic field extraction in lookup_class. Model still doesn't call lookup_class for QueryRequest/ChatRequest so fields never enter context. Disk grep may have found wrong files (core/engine.py vs engines/.../engine.py). Reverted grep, kept field extraction. 5 runs: 35, 37, 41, 36, 41. |
| 31a-d | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `68a1dda4` | `81b5abf` | DIFFERENT TASK: "token usage tracking" (post-audit refactor) | 12 | 294-342s | 6.8 | 6.2 | 4.4 | 4.0 | 4.2 | 6.0 | **30.8 avg (23-36)** | Different benchmark task ("token tracking" not "streaming"). Not comparable to runs 1-30. Lower scores reflect harder task, not code regression. 4 plans: 23, 31, 36, 33. |
| 32a-b | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `68a1dda4` | `81b5abf` | post-audit refactor verification (streaming task) | 12 | 289-330s | — | — | — | — | — | — | **33.5 avg (33-34)** | Post-refactor code on streaming task. Scored 34, 33. Initially thought regression but later verified as variance (v0.5.0 also produced 33). |
| 33a-c | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `b65e6c84` | `81b5abf` | v0.5.0 verification (reverted source) | 12 | 267-298s | — | — | — | — | — | — | **41.7 avg (33-47)** | Reverted to v0.5.0 to verify. Produced 45, 33, 47 — confirming 33 is in the normal distribution, not a refactor regression. |
| 34a-c | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `68a1dda4` | `81b5abf` | post-audit refactor (restored) | 12 | 291-356s | — | — | — | — | — | — | **40.7 avg (39-42)** | Refactored code: 39, 41, 42. Ceiling at 42 vs v0.5.0's 47. Reverted refactor, kept tests. |
| 35a-e | 2026-03-29 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `56f411e1` | `81b5abf` | synthesis prompt fix: "trace call chain, don't skip layers" | 12 | 258-306s | 7.6 | 8.0 | 6.0 | 5.6 | 5.4 | 7.6 | **40.6 avg (37-47)** | Prompt fix raised floor from 33 to 37. Prevented shortcut architecture. 5 runs: 47, 37, 42, 38, 39. |
| 36a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `9f4bd8c6` | `81b5abf` | + artifact retry when coverage < 50% of needed | 12 | 284-331s | 8.0 | 8.0 | 6.4 | 6.0 | 5.6 | 7.2 | **43.2 avg (32-47)** | Retry fired on plan 4 (0→2 artifacts) but quality was low (scored 32). Four plans hit 45-47 — best consistency yet. 5 runs: 47, 46, 42, 32, 45. |
| 37a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `9f4bd8c6` | `81b5abf` | + improved retry (component interfaces in retry context) | 12 | 266-331s | 7.8 | 8.0 | 6.2 | 7.0 | 5.6 | 8.0 | **42.6 avg (33-48)** | Three plans at 47-48 (best ceiling). Retry fired on plan 1 (1→5 artifacts). But floor still 33 — plan 2 had self-contradicting decisions + fabricated AnswerMode.NORMAL. Floor is decision quality, not artifact coverage. 5 runs: 47, 33, 48, 38, 47. |
| 38a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `823a06b7` | `81b5abf` | + AST quality gate v1 (retry on ≥3 fabricated methods) | 12 | 260-331s | 7.2 | 8.2 | 6.0 | 5.2 | 5.0 | 7.4 | **39.0 avg (35-44)** | AST gate fired once (run 2: 3→3 violations, kept original). Retry can't fix fabrication because model doesn't know WHAT to use instead. Floor 35 (better than 33 but ceiling dropped to 44). 5 runs: 39, 42, 44, 35, 35. |
| 39a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `d5c3b8dd` | `81b5abf` | + AST quality gate v2 (real method names in retry) | 12 | 262-319s | 7.6 | 8.0 | 5.6 | 5.4 | 5.6 | 7.8 | **40.0 avg (33-46)** | AST gate now shows real methods from structural index. Plan 1: 3→0 violations (retry worked!) but scored only 38 — scorer catches deeper issues AST misses. Gate adds complexity without raising avg. Reverted. 5 runs: 38, 33, 45, 46, 38. |
| 40a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `da72f377` | `81b5abf` | + P1: post-decomp coverage gate (retry if interior layers uncovered) | 12-13 | — | 8.2 | 7.4 | 6.8 | 5.6 | 5.6 | 8.4 | **42.0 avg (36-49)** | Gate fired on ALL 5 runs — model always skips interior layers on first try. Floor 33→36 (+3). Ceiling 49 (new best). Avg 42.0 ≈ 42.6 baseline (within noise). Residual floor issue: even with corrected decisions, artifacts still fabricate engine internals (_retrieval, _build_context). 5 runs: 36, 49, 38, 39, 48. |
| 41a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `da72f377+P2` | `81b5abf` | + P2: synthesis layer warning (inject uncovered files into gathered_context) | 12-13 | — | 8.4 | 7.6 | 6.6 | 6.6 | 6.6 | 8.4 | **44.2 avg (37-53)** | P1 still fired all 5 runs. P2 injected silently. Alignment +1.0, Implementability +1.0 vs run 40. Ceiling 53 (new record). Floor 37 (+1 vs P1 alone). Residual floor: decision contradictions — plan 4 d14 (per-token governance) contradicts synthesis (pre-streaming); plan 5 d4 claims providers lack chat_stream (they don't). Exactly P3's target. 5 runs: 51, 41, 53, 39, 37. |
| 42a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `da72f377+P2+P3bug` | `81b5abf` | + P3: contradiction detection (buggy — model returned wrong JSON key names) | 12-13 | — | 7.8 | 8.4 | 6.8 | 6.2 | 6.2 | 8.6 | **44.0 avg (38-51)** | P3 was a no-op: model returned contradictions with keys like "a"/"b" instead of "decision_a"/"decision_b" — all 6 contradictions in plan 2 skipped. Scores identical to run 41 (within noise). Bug fixed in same session: robust fallback parsing (key aliases + regex scan). Floor 38 (+1 vs 41, within noise). Need run 43 to test fixed P3. 5 runs: 42, 40, 51, 38, 49. |
| 43a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `da72f377+P2+P3fix` | `81b5abf` | + P3 fixed: robust key parsing (aliases + regex scan) | 12-14 | — | 8.4 | 7.4 | 6.6 | 6.0 | 6.2 | 8.4 | **43.0 avg (39-52)** | P3 fired on plan 3 only (4 contradictions, d4 retried 3x + d14 once) — plan 3 scored 52. P3 did NOT fire on the floor plans (39, 39). Floor issues are synthesis hallucinations: wrong field names (request.messages vs request.history), non-existent internal methods (_retrieve, _build_context), synthesizer.py treated as new when it exists. P3 can't fix synthesis-layer errors. Floor: 39 (+2 vs run 41, marginal). Avg essentially flat. 5 runs: 42, 39, 52, 39, 43. |
| # | Date | Model | Quant | Ctx | Pipeline SHA | Codebase SHA | Pipeline | Decisions | Time | Files | Contract | Consistency | Alignment | Implement | Scope | **Total** | Notes |
| 44a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `ee1e8a5a+P4v1` | `81b5abf` | + P4: post-synthesis field grounding (Pydantic fields + self._X attrs) | 12-13 | — | 7.8 | 8.4 | 6.8 | 6.2 | 6.8 | 8.0 | **42.0 avg (37-53)** | P4 said "no corrections" on ALL 5 plans. Grounding validator still found violations. Fix: expanded P4 + added grounding-repair pass. 5 runs: 38, 43, 53, 39, 37. |
| 45a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `ee1e8a5a+P4v2+repair` | `81b5abf` | + P4 expanded (method names) + grounding repair (AST violations → targeted LLM fix) | 12-15 | — | 5.8 | 7.4 | 4.6 | 3.8 | 3.8 | 6.4 | **36.4 avg (28-49)** | Bimodal: plans 1-2 strong (49, 45), plans 3-5 floor (28, 30, 30). Confirmed as variance in run 46. 5 runs: 49, 45, 28, 30, 30. |
| 46a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `ee1e8a5a+P4v2+repair+skipfix` | `81b5abf` | + false-positive fix: HTTPException + FastAPI classes added to grounding _SKIP_NAMES | 12-15 | — | 7.8 | 8.6 | 6.0 | 7.0 | 6.4 | 8.0 | **43.6 avg (40-47)** | Best floor ever: 40. Confirms run 45 was variance. 5 runs: 42, 47, 43, 40, 46. |
| 47a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `3d935ba7+P4removed` | `81b5abf` | P4 removed (dead weight). Grounding repair only. | 12-14 | — | 7.4 | 8.6 | 5.0 | 5.8 | 5.4 | 7.4 | **39.6 avg (36-49)** | P4 removal safe — ceiling held at 49. 5 runs: 36, 36, 49, 41, 36. |
| 48a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `3d935ba7+P4removed` | `81b5abf` | same as 47 (confirmation run) | 12-14 | — | 8.0 | 7.5 | 5.8 | 6.8 | 5.5 | 8.0 | **41.5 avg (33-49)** | 4/5 plans (1 DNF). Combined 47+48: avg 40.4. 4 runs: 39, 45, 33, 49. |
| 49a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `3d935ba7` | `81b5abf` | P4 re-enabled (A/B test vs runs 47-48) | 12-14 | — | 7.4 | 8.2 | 5.2 | 5.6 | 4.8 | 7.2 | **39.0 avg (36-45)** | A/B test: P4 re-enabled scored 39.0 — LOWER than without. P4 confirmed dead weight. 5 runs: 36, 45, 36, 38, 40. |
| 50a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `10a9e8f6+chaincheck` | `81b5abf` | Chain call grounding (self._attr.method()) — REVERTED | 12-14 | — | 7.2 | 7.4 | 6.0 | 5.0 | 4.8 | 6.8 | **38.2 avg (29-46)** | False positives: structural index only covers 30-file subset. Real methods on un-indexed classes flagged + repaired incorrectly. 5 runs: 46, 43, 43, 29, 30. |
| 51a-e | 2026-03-30 | qwen3-coder-next-reap (40B) | Q5_K_S | 65K | `10a9e8f6+enriched` | `665388d` | Enriched repair: "Available methods" list in violations — REVERTED | 12-15 | — | 5.6 | 7.4 | 6.2 | 3.4 | 3.8 | 5.6 | **33.2 avg (29-44)** | REGRESSION. LLM picks semantically wrong real methods from list. Plans with repair: 29-33. Plan without: 44. 5 runs: 30, 44, 33, 29, 30. |


### Column Key

| Column | Description |
|--------|-------------|
| Model | LLM model used for planning |
| Quant | Quantization level |
| Ctx | Context window size |
| Pipeline SHA | fitz-graveyard commit hash (pipeline code) |
| Codebase SHA | fitz-sage commit hash (target codebase being planned against) |
| Pipeline | Pipeline variant (monolithic v1, decomposed v4, etc.) |
| Decisions | Number of atomic decisions decomposed |
| Time | Total wall clock time |
| Files | file_identification score (1-10) |
| Contract | contract_preservation score (1-10) |
| Consistency | internal_consistency score (1-10) |
| Alignment | codebase_alignment score (1-10) |
| Implement | implementability score (1-10) |
| Scope | scope_calibration score (1-10) |
| Total | Sum of 6 dimensions / 60 |
| Notes | Key observations — what went right/wrong |

---

## Change Log

Track pipeline or codebase changes that affect comparability between runs.

| Date | Component | SHA | Change | Expected Impact |
|------|-----------|-----|--------|-----------------|
| 2026-03-27 | — | — | Baseline run — no changes | — |
| 2026-03-28 | synthesis.py | — | Source code injection (27K chars) into artifact extraction | HURT (32 vs 36). Too much context confused model. |
| 2026-03-28 | synthesis.py | — | Compact cheat sheet (4K, class/method names only) | HELPED slightly (+1). Less noise than source dump. |
| 2026-03-28 | synthesis.py | — | Template-constrained: auto-extract instance attrs from __init__ via AST | HELPED (+2.2 mean, +4 ceiling). Model uses real attr names 10x more. |
| 2026-03-28 | indexer.py | — | Method flow extraction (extract_method_flows) — NOT wired in | HURT when wired (both in index and cheat sheet). Available as utility. |
| 2026-03-28 | decision_resolution.txt | — | Ban line numbers in evidence citations | NO EFFECT within variance. |
| 2026-03-28 | indexer.py | — | Add param names to class method signatures | NO EFFECT within variance. |
| 2026-03-28 | artifact_resolution.py | — | New stage: per-artifact LLM calls from resolutions + source code | HURT (-3.3 mean). More detailed code = more fabrication surface. |
| 2026-03-28 | artifact_resolution.py | — | Fixed: cap at needed_artifacts only, no full-file rewrites | Still HURT (-3.3). Detailed artifacts with wrong method names. |

## Conclusions (as of 2026-03-28)

**Winner: Template-constrained extraction** (run 15, mean 37.8/60)
- Auto-extracts instance attributes from __init__ via AST
- Injects compact list of `self._xxx = ClassName(...)` into artifact extraction prompt
- Model uses real attribute names instead of fabricating

**What we learned about this model (qwen3-coder-next-reap 40B Q5_K_S):**
- Contract preservation is reliably strong (7-9, mean 8.0)
- File identification is solid (6-8, mean 7.3)
- Scope calibration is good (6-8, mean 7.3)
- Codebase alignment is the bottleneck (3-8, mean 4.8) — model fabricates method names
- More context HURTS: source dump (27K), method flows, detailed artifacts all regressed
- Less is more: compact cheat sheet (4K) > source dump (27K) > method flows
- The model has a fixed context budget — any additional info displaces something useful

**Root cause of fabrication:**
- Synthesis writes prose ("build context from chunks")
- Extraction materializes prose into code (`self._build_context()` — doesn't exist)
- Resolution stage gets it RIGHT (reads source, cites real attrs)
- But synthesis loses the grounding by abstracting to prose
- Direct code generation from structural index: 0 fabrications in 10 isolated runs
- BUT when the model writes longer, more detailed code: MORE fabrication, not less

---

## Run Details

### Run 1 — 2026-03-27 — qwen3-coder-30b-a3b Q6 — decomposed v4

**Results dir:** `benchmarks/results/decomposed_20260327_211705/`

**Stage timings:**
- Agent gathering: 21s
- Implementation check: 5s
- Call graph: 1s
- Decision decomposition: 7s (5 decisions)
- Decision resolution: 11s (5 decisions, ~2s each)
- Synthesis: 142s (bulk of the time)
- Coherence check: 3s

**What the plan got right:**
- Correctly identifies LLM providers already have `chat_stream()`
- Correctly identifies FastAPI `StreamingResponse` as the delivery mechanism
- Finds the right provider-layer files (base.py, openai.py, anthropic.py, cohere.py, ollama.py)

**What the plan got wrong:**
- Treats streaming as a purely API-layer change (modifying only schemas.py + query.py)
- Completely misses the engine pipeline: `API → FitzService.query() → FitzKragEngine.answer() → CodeSynthesizer.generate() → ChatProvider.chat()` — none of these intermediate layers support streaming
- Decision d4 claims the chat endpoint "has no implementation" — factually wrong, it's fully implemented
- Decision d3 omits OllamaChat and EnterpriseChat from providers that implement `chat_stream()`
- Artifacts destructively rewrite schemas.py (removes 6+ model classes) and query.py (replaces working code with `pass` stubs)
- Despite repeated constraint "existing method chat() must not be modified", the artifacts violate this

**Root cause hypothesis:** The model sees the provider files have `chat_stream()` and jumps straight to the API layer, never tracing the actual call chain through service→engine→synthesizer. The call graph has this information but the model doesn't follow it deep enough during resolution.

### Run 2 — 2026-03-27 — nemotron-cascade-2-30b-a3b i1 — decomposed v4

**Results dir:** `benchmarks/results/decomposed_20260327_213547/`
**Result: DNF — total failure**

**Stage timings:**
- Agent gathering: 12s
- Implementation check: 6s (745 chars — this worked)
- Call graph: 1s
- Decision decomposition: 19s (7 decisions, 2431 chars — this worked)
- Decision resolution: 132s (7 decisions, ALL returned 0 chars)
- Synthesis: 417s (all 0 chars — every extraction failed)
- Coherence check: 74s (0 chars)

**What happened:** The model can produce output for simple prompts (impl check, decomposition) but returns 0 chars for every structured output call (resolution, synthesis extractions, critique). Each call takes ~18-20s of "thinking" then returns nothing.

**Root cause:** Pipeline sends `enable_thinking: false` in `extra_body.chat_template_kwargs`. Nemotron Cascade is a reasoning model — it may require thinking tokens to produce output, or LM Studio strips thinking tokens and the model's non-thinking output is empty. The ~18s per call suggests the model IS generating tokens (thinking), but they get discarded.

**Action needed:** Either remove `enable_thinking: false` for this model, or accept Nemotron Cascade is incompatible with the current pipeline's chat template kwargs.

### Run 3 — 2026-03-27 — nemotron-cascade-2-30b-a3b i1 — decomposed v4 (thinking enabled)

**Results dir:** `benchmarks/results/decomposed_20260327_214935/`
**Result: DNF — same failure even with thinking enabled**

Commenting out `enable_thinking: false` made no difference. Still 0 chars on every call after decomposition. The impl check (966 chars) and decomposition (1744 chars, 5 decisions) both produce output, but all resolution/synthesis calls return empty.

**Root cause (updated):** This is NOT a chat_template_kwargs issue. LM Studio streams Nemotron Cascade's reasoning tokens into a different field (`reasoning_content` or similar) instead of `delta.content`. The pipeline's streaming loop only reads `delta.content`, so it sees 0 chars. The model IS generating output — it takes ~18s per call — but it's going somewhere the pipeline doesn't look.

**Fix would require:** Modifying the LM Studio client's streaming loop to also capture `reasoning_content` from chunks. But this is a model-specific quirk, not a pipeline bug. Nemotron Cascade is incompatible with the current streaming approach.

### Run 4 — 2026-03-27 — nemotron-cascade-2-30b-a3b i1 — decomposed v4 (reasoning_content captured)

**Results dir:** `benchmarks/results/decomposed_20260327_230312/`
**Result: DNF — `<SPECIAL_30>` token flood**

Added `reasoning_content` capture to the LM Studio streaming loop (content_parts vs reasoning_parts, prefer content, fall back to reasoning). The model now produces output — but it's 49,140 chars of `<SPECIAL_30>` repeated per call. This is Nemotron Cascade's internal cascade reasoning token representation leaking through LM Studio's OpenAI-compatible API.

The model uses opaque special tokens for its cascade reasoning that are not meant to be readable text. The actual answer (if any) goes to `content`, but `content` is empty because the model burns its entire token budget on reasoning tokens.

**Conclusion:** Nemotron Cascade 2 is fundamentally incompatible with the OpenAI-compatible chat completions API. It needs its native API or a server that properly handles the cascade reasoning protocol. Closing this model investigation.

**Pipeline fix shipped:** LM Studio client now separates `content_parts` and `reasoning_parts`, prefers content, falls back to reasoning only if readable (discards `<SPECIAL_` tokens). This makes the pipeline robust for future reasoning models like DeepSeek-R1 that put real text in `reasoning_content`.

### Run 5 — 2026-03-27 — qwen3-coder-next 80B IQ3_S — decomposed v4

**Results dir:** `benchmarks/results/decomposed_20260327_234003/`

**Stage timings:**
- Agent gathering: 8s
- Implementation check: 5s
- Call graph: 1s
- Decision decomposition: 17s (15 decisions — 3x more than 30B's 5)
- Decision resolution: 101s (15 decisions, ~5-10s each, ~80-130 tok/s)
- Synthesis: 214s
- Coherence check: 3s

**What the plan got right:**
- Correct architecture: parallel streaming methods alongside existing, new SSE endpoint, governance runs before streaming
- All core files identified (base.py, engine.py, synthesizer.py, query.py, schemas.py, fitz.py, decider.py, feature_extractor.py)
- Strong contract preservation — explicitly preserves /query, /chat, fitz.query(), Answer, StreamingChatProvider
- Proposes stream_answer() alongside answer(), not modifying existing methods
- Correctly identifies that GovernanceDecider.decide() is batch-only and must complete before streaming

**What the plan got wrong:**
- Artifacts reference nonexistent methods: `get_service().get_engine()`, `self._build_context()`, `self._build_messages()`, `self._ensure_engine()`
- Uses `request.question` but ChatRequest field is `request.message`
- Calls `extract_features()` with wrong signature
- Misses router registration in `app.py` and `routes/__init__.py`
- Decision d11 has corrupted JSON-within-JSON formatting
- Roadmap critical_path references phase 4 but only 3 phases defined

**Key insight:** The 80B model understands the architecture much better than the 30B (scores 36 vs 20), but still hallucinates method names and signatures in the implementation artifacts. The gap is in low-level codebase grounding, not architectural reasoning.

### Run 23 — 2026-03-29 — Pre-fill class lookups in prompt

**Results dir:** `benchmarks/results/decomposed_20260329_014932/`

**What changed:** Added `_pre_fill_class_lookups()` method that:
1. Extracts CamelCase class names from decision resolutions + synthesis reasoning
2. Calls `lookup_class` for each (found 7/26 real classes, 2620 chars)
3. Injects results directly into the tool prompt as "PRE-GATHERED CLASS INFO"
4. Seeds dedup cache for both `lookup_class` and `check_exists` calls
5. Prompt tells model: "You already have the key class info — produce the artifact JSON"

**What happened:**
- Model skipped tools ENTIRELY: 0 rounds, 0 tool calls, artifacts in 15.8s
- Pre-fill gave the model a shortcut and it took it — no verification at all
- Artifacts still have fabrications (wrong field names, missing methods)
- Quality dropped to template-fallback level (37.2 avg vs 37.8 baseline)
- Variance spiked (stdev 6.1, worst of all runs)

**Per-run scores:** 31, 40, 38, 46, 31

**Key insight:** The warm-up tool rounds weren't waste — they WERE the verification. When tools work, the model checks things before writing and scores 45. Pre-filling the info and telling it to "just write" produces the same quality as template-constrained extraction. The tool-use PROCESS (model actively verifying) is what creates quality, not the tool results themselves.

**What this rules out:**
- ~~Option 1 from handoff: pre-fill critical lookups~~ — causes model to skip verification
- ~~Option 5: nuclear option (skip tools, inject context)~~ — same problem, no verification loop

**What this points toward:**
- The model needs to CALL tools itself (active verification > passive context)
- The degeneration problem (check_exists spam) must be solved without removing tools
- Reducing tools (remove check_exists) + shorter max_rounds is the next experiment

---

## Session Handoff

### Session 2026-03-27/29 — Evaluation System + Pipeline Optimization

**What was built:**
1. **Sonnet-as-Judge evaluation system** (`benchmarks/eval_*.py`) — scores plans on 6 dimensions via Claude Code subagents. No Anthropic SDK needed.
2. **Post-synthesis grounding validator** (`fitz_graveyard/planning/validation/grounding.py`) — AST path checks fabricated methods/classes, LLM path checks architectural gaps.
3. **Template-constrained cheat sheet** — auto-extracts instance attrs from `__init__` via AST, injects into artifact extraction prompt. Also resolves component class methods from source on disk.
4. **Full-signature evidence** — resolution prompt demands complete param lists (no `...`), parallel methods must match original params.
5. **Tool-assisted artifact building** (`fitz_graveyard/planning/pipeline/tools/codebase_tools.py`) — 4 lookup tools (lookup_method, lookup_class, check_exists, read_method_source) used during artifact generation via `generate_with_tools()`.
6. **Method flow extractor** (`indexer.py:extract_method_flows`) — AST-based pipeline step extraction. Built but NOT wired in (caused regressions when tested).

**Current best config (run 21, mean 41.4/60):**
- Model: `qwen3-coder-next-reap-40b-a3b-i1` (reaped 40B at Q5_K_S)
- Pipeline: decomposed v4 + full-sig evidence + template-constrained attrs + tool-assisted artifacts
- When tools work (40% of runs): scores 45/60
- When tools exhaust (60%): falls back to template, scores ~39/60

**Score progression:**
```
20/60  → 30B Q6 baseline
35.6   → 40B reaped baseline (model upgrade)
37.8   → + template-constrained attrs
39.2   → + full-signature evidence in resolutions
41.4   → + tool-assisted artifact building (run 21)
39.6   → + smart exit dedup (run 22, lowest variance 2.7)
37.2   → + pre-fill in prompt (run 23, REGRESSION — model skipped tools)
38.2   → + remove check_exists + max5 (run 24, no degeneration but variable)
37.4   → + tool history pre-fill + module strip + forced exit (run 25, lowest stdev 2.5)
37.0   → + 3 rounds after pre-fill (run 26, extra source reading didn't help)
43.4   → + tool-enriched template (run 28, NEW BEST — tools gather, template extracts)
39.0   → + baseline pre-call (run 29, REGRESSION — pre-fill always hurts)
38.0   → + disk grep + Pydantic fields (run 30, REGRESSION — wrong files found)
40.6   → + synthesis prompt fix (run 35, floor 33→37 but avg dropped)
43.2   → + artifact coverage retry (run 36, four plans at 45-47)
42.6   → + improved retry with component interfaces (run 37, ceiling 48)
39.0   → + AST quality gate v1 (run 38, REGRESSION — retry adds complexity)
40.0   → + AST quality gate v2 with real methods (run 39, reverted)
```

**Tool reliability engineering — solved problem, wrong bottleneck:**

Over runs 22-25, tool reliability went from 40% to 100%. Key changes:
1. **Remove check_exists** (run 24) — eliminated the biggest degeneration source (15+ useless calls)
2. **Module path stripping** (run 25) — `fitz_sage.sdk.fitz.Fitz` → `Fitz`, fixing wasted rounds on fully-qualified names
3. **Pre-fill as tool history** (run 25) — inject key class lookups as fake tool-call messages, model starts in verification mode
4. **Forced exit after N rounds** (runs 24-25) — prevents infinite research loop

But scores stayed at 37-38 avg despite 100% tool reliability. The bottleneck shifted:

**The REAL bottleneck: artifact code body fabrication**

Tools give the model WHAT EXISTS (class structures, method signatures). But the model still fabricates IMPLEMENTATION DETAILS:
- Wrong field names: `request.query` instead of `request.question`
- Wrong imports: `from fitz_sage.api.models.query` instead of `schemas`
- Fabricated helpers: `self._build_messages()`, `self._retrieve()`
- Wrong constructor params

These are in the METHOD BODY, not the interface. Tools verify interfaces but can't prevent the model from inventing internals. The model has 40B parameters trying to write code for a codebase it doesn't fully understand — some fabrication is inevitable.

**What worked in run 21's 45-scoring plans (and didn't in runs 23-25):**

Run 21's best plans had the model voluntarily produce JSON after organically researching for 3-4 rounds. The model's internal "I'm ready" signal led to more careful output than forced exits. But the model NEVER produces JSON voluntarily in subsequent runs — it always exhausts rounds. The natural JSON production in run 21 may have been model variance, not reproducible behavior.

**Current best config (runs 36/37, commit `9f4bd8c6`):**

Three changes from v0.5.0 baseline:
1. **Tool-enriched template** (run 28) — tools gather verified class/method info, template extraction uses enriched context
2. **Synthesis prompt fix** (run 35) — "trace the call chain from entry point to implementation, don't skip layers"
3. **Artifact coverage retry** (run 36) — when extracted artifacts < 50% of needed_artifacts, retry with missing file hints + component interfaces

Results across all runs with this config:
- Run 36: avg 43.2, range 32-47, four plans at 45-47
- Run 37: avg 42.6, range 33-48, three plans at 47-48

**What was tried and ruled out (sessions 2026-03-29/30):**

| Approach | Run | Result | Why it failed |
|----------|-----|--------|---------------|
| Pre-fill in prompt | 23 | 37.2 | Model skipped tools entirely (0 rounds) |
| Remove check_exists only | 24 | 38.2 | No degeneration but variable research quality |
| Pre-fill as tool history + forced exit | 25 | 37.4 | Forced exit uses inferior client.generate() path |
| Pre-fill + 3 rounds + source reading | 26 | 37.0 | Extra source doesn't help |
| Pre-fill + no forced exit (silent dedup) | 27 | DNF | Model loops forever on pre-filled duplicates |
| **Tool-enriched template** | **28** | **43.4** | **Breakthrough — tools gather, template extracts** |
| Baseline pre-call (seed dedup cache) | 29 | 39.0 | Pre-fill seeds dedup → earlier stale exit |
| Disk grep + Pydantic fields | 30 | 38.0 | Disk grep found wrong files |
| Refactored code (constants + exceptions) | 32-34 | 33-42 | Ceiling dropped from 48 to 42, reverted |
| Synthesis prompt fix | 35 | 40.6 | Floor rose 33→37, ceiling held at 47 |
| + Coverage retry | 36 | 43.2 | Four plans at 45-47, retry saved 0→2 artifact case |
| + Improved retry (component interfaces) | 37 | 42.6 | Three plans at 47-48 |
| + AST quality gate v1 | 38 | 39.0 | Retry can't fix fabrication without real method info |
| + AST quality gate v2 (real methods) | 39 | 40.0 | Gate worked (3→0 violations) but scorer catches deeper issues |

**The floor problem (33-35) — root cause:**
5-why analysis traced the floor to **decision resolution quality**, not artifact extraction:
1. Low score → thin artifacts (1-2 files, fabricated methods)
2. Thin artifacts → thin synthesis reasoning (few components)
3. Thin synthesis → shallow needed_artifacts list (2 instead of 5)
4. Shallow list → decisions took a shortcut architecture (API→provider.chat_stream directly, bypassing engine/synthesizer)
5. Shortcut decisions → model non-determinism in resolution quality (5/12 resolutions mention stream methods vs 11/12 in good plans)

The prompt fix addresses this partially ("trace the call chain"). The coverage retry catches missing artifacts. But when the decisions themselves are self-contradicting (e.g., d9 contradicts d2), no amount of artifact retry can fix the plan. This is a model capability limitation at 40B Q5_K_S.

**Key engineering in production:**
1. `_strip_module()` — handles fully-qualified names (fitz_sage.sdk.fitz.Fitz → Fitz)
2. `_find_source` disk fallback with filename matching
3. check_exists removed from tool list — eliminated degeneration
4. Normalized dedup cache keys — module-path variants caught
5. Early stale exit (2 consecutive duplicate rounds) → template fallback
6. Tool results formatted as "VERIFIED CODEBASE INFO" and injected into template context
7. `_build_artifacts_with_tools` returns `(artifacts, tool_context)` tuple
8. Synthesis prompt: "trace the call chain, don't skip layers"
9. Artifact coverage retry with component interface hints
10. Pydantic field extraction in lookup_class (AnnAssign nodes)

**How to run the benchmark:**
```bash
# Model: qwen3-coder-next-reap-40b-a3b-i1 in LM Studio at 65K context
# Use --parallel 2 for concurrent benchmark runs (see below)
lms load qwen3-coder-next-reap-40b-a3b-i1 -y -c 65536 --parallel 2

# Run N plans, 2 at a time (streaming task against fitz-sage codebase)
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
  --runs 5 -p 2 \
  --source-dir ../fitz-sage \
  --context-file benchmarks/ideal_context.json \
  --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
  --score

# Score via Claude Code subagents (read score_prompt_NN.md files)
# Results go to benchmarks/results/decomposed_YYYYMMDD_HHMMSS/
```

IMPORTANT: The `--query` MUST be the streaming task above. The default query is "Add token usage tracking" which is a DIFFERENT task and scores are NOT comparable (run 31 used the wrong query and scored 30.8).

### Session 2026-03-30 — Parallel Benchmark Runs

**Problem:** Each 5-run benchmark takes ~1500s (5 x ~300s sequential). GPU utilization during inference is only ~70% — headroom exists for concurrent request batching.

**Experiment:** Tested LM Studio concurrent request throughput at N=1,2,3,4 with `benchmarks/test_parallel_throughput.py`. Each test sends N identical-length architectural design prompts (2048 max tokens) concurrently and measures wall time + per-request tok/s.

**Results (RTX 5090 32GB, qwen3-coder-next-reap 40B Q5_K_S, 65K context):**

| N | Throughput | Gain | Per-req tok/s | TTFT |
|---|-----------|------|---------------|------|
| 1 | 150.6 t/s | 1.00x | 158.9 | 0.76s |
| 2 | 209.1 t/s | 1.39x | 114.0 | 1.80s |
| 3 | 234.2 t/s | 1.56x | 83.6 | 1.90s |
| 4 | 249.0 t/s | 1.65x | 67.1 | 2.59s |

**Analysis:**
- N=2 is the sweet spot: +39% throughput, each request only 28% slower, TTFT still under 2s
- N=3: marginal gain (+17% over N=2) but per-request drops to 84 tok/s
- N=4: almost no gain over N=3 (+6%), per-request tanks to 67 tok/s, TTFT 3.4x worse
- Scaling is sub-linear — model is memory-bandwidth bound, all requests compete for VRAM bandwidth to read weights
- LM Studio does real continuous batching (not queuing) — both requests stream tokens simultaneously

**What was built:**
- `benchmarks/test_parallel_throughput.py` — standalone throughput scaling test (N=1..4)
- `--parallel-runs` / `-p` flag on `decomposed` command in `plan_factory.py` — runs N plans concurrently in batches

**Impact on benchmarking:**
- 5 runs with `-p 2`: 3 batches (2+2+1) instead of 5 sequential → ~1050s vs ~1500s (~30% wall time reduction)
- No quality impact — each run gets its own client, config, pipeline instance

**Requirements:**
- LM Studio must be loaded with `--parallel N` matching the `-p N` flag
- `lms load ... --parallel 2` for `-p 2`

### Session 2026-03-30 — P4, Grounding Repair, and What Failed

**Baseline entering session:** P1+P2+P3 committed (`ee1e8a5a`), avg ~43-44, floor 39.

**What shipped (still active):**

1. **Grounding repair** (`repair_violations()` in `grounding.py`): one LLM call per affected artifact, fed exact AST violation messages ("Method '_prepare_query' not found"), asks for replacements. Wired in `orchestrator.py` after `validate_grounding()`. Updates `prior_outputs["design"]["artifacts"]` in-place.
2. **False-positive fix**: HTTPException + FastAPI form/body classes added to `_SKIP_NAMES` in grounding.py.
3. **fitz_sage → fitz_sage rename**: all imports and path references updated for the fitz-sage package rebrand.

**What was tried and reverted:**

1. **P4 field grounding** (runs 44-49): LLM self-audit of artifacts against extracted Pydantic fields + method lists. Said "no corrections" on ALL plans across ALL runs. A/B test (run 49 with P4 vs runs 47-48 without): 39.0 with vs 40.4 without. **Killed** — dead weight, 13-16s wasted per plan.
2. **Chain call grounding** (run 50): extended AST check to catch `self._attr.method()` patterns. False positives because structural index only covers 30-file subset — methods on un-indexed classes (GovernanceDecider.decide, ContextAssembler.assemble) flagged as missing. Repair corrupted correct code. **Reverted.**
3. **Enriched repair with "Available methods"** (run 51): included real method list in violation messages so repair LLM could pick correct replacements. **Backfired** — LLM picks semantically wrong but syntactically valid methods (_build_prompt → _build_row). Plans with enriched repair: 29-33. Plan without: 44. **Reverted.**

**Current baseline:** `55386ee2`, avg ~40 (runs 47-49 combined: 40.4), floor ~36. Active: P1 coverage gate, P2 synthesis warnings, P3 contradiction detection, grounding repair with bare violation messages.

**Why the floor is ~33-36 (not addressable by post-synthesis fixes):**

All floor plans share the same root cause: **decision-level architectural misreads**. The model:
- Confuses eval tools (`GovernanceClassifier` in tools/governance/) with production governance
- Claims providers lack `chat_stream()` when all 4 already have it
- Fabricates engine internals (`_retrieve`, `_build_messages`, `_analyze_and_retrieve`)
- Confuses `engine.chat()` with `engine.answer()` (chat doesn't exist)

These errors enter at `decision_resolution`, not synthesis. No post-synthesis repair can fix them because the plan's architecture is wrong upstream.

**Promising next directions (not yet tried):**

1. **Decision trace injection**: serialize resolved decisions into a compact registry, inject into synthesis prompt, validate synthesis doesn't contradict it. Targets consistency (weakest dimension at ~5.0).
2. **Phase count auto-correction**: deterministic post-synthesis check — count phases defined vs total_phases field, strip references to non-existent phases. Easy win for consistency.
3. **Full codebase index for grounding**: the structural index only covers 30 files. A full index would eliminate false positives in chain call checking. Requires changes to `build_structural_index` or a separate full-scan pass.
4. **Decision-level architecture sanity check**: after decomposition, verify key claims ("does synthesizer.py exist?") against structural index before resolution proceeds. Could prevent the most common floor-plan failure.

**Critical files to read first in new session:**
- This file (streaming-task-tracker.md) — the run log tells the full story
- `benchmarks/BENCHMARK.md` — how to run benchmarks, sequential 1-1-1-2 assessment protocol
- `fitz_graveyard/planning/validation/grounding.py` — AST grounding validator + `StructuralIndexLookup` + `repair_violations()`. The `_SKIP_NAMES` set prevents false positives for framework classes.
- `fitz_graveyard/planning/pipeline/orchestrator.py` — repair hook after `validate_grounding()` at ~line 917.
- `fitz_graveyard/planning/pipeline/stages/synthesis.py` — synthesis stage. P4 was deleted from here. Key methods: `_build_artifacts_with_tools`, `execute`.
- `fitz_graveyard/planning/pipeline/stages/decision_decomposition.py` — P1 coverage gate.
- `fitz_graveyard/planning/pipeline/stages/decision_resolution.py` — P3 contradiction detection.
