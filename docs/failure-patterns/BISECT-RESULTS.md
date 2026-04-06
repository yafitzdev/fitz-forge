# Bisect Results: Score Regression Analysis (2026-04-06)

## Summary

After run 67 (45.3 avg), a series of F10-focused commits caused a score regression to ~35. A bisect across 6 commit checkpoints (30+ scored plans, ~50 Sonnet scoring agents) identified the root cause and reverted to a clean baseline.

**Current state: commit I (E + bug fixes) scores 40.7 avg — matching baseline.**

## Bisect Table

| Commit | Hash | Description | Plans | Avg Score | Delta |
|--------|------|-------------|-------|-----------|-------|
| **A** | `1bf4470f` | Baseline (best-of-3 scope consensus) | 8 | **42.0** | — |
| B | `b3d23b5d` | + F10 compose rule + genericize prompts | skipped | — | — |
| **C** | `1f9549f1` | + Split reasoning into design + roadmap_risk | 8 | **40.8** | -1.2 |
| D | `ee09867d` | + F20 decision merger (union-find) | skipped (included in E) | — | — |
| **E** | `17b50689` | + Refinement pass + decision merger | 8 | **41.9** | -0.1 |
| **F** | `67032f5a` | **+ Generic decision filter** | 7 | **36.3** | **-5.7** |
| **G** | `7ff9d742` | + F10 corrector + class cache + all fixes | 7 | **38.0** | -4.0 |
| **H** | (HEAD-filter) | G minus decision filter | 7 | **~34.6** | -7.4 |
| **I** | (current) | **E + bug fixes only** | 6 | **40.7** | -1.3 |

## Root Cause: Decision Filter (Commit F)

The generic decision filter (`_filter_fabricated_from_reasoning` applied to `relevant_decisions`) was the primary regression cause. It stripped `object.method()` references from decisions before passing them to the artifact prompt.

**Why it hurt**: Decisions contain method names the model needs to write correct artifacts. When the filter stripped `service.query_stream()` from a decision, it also stripped legitimate references like `engine.answer()`, `service.query()`, `self._synthesizer.generate()`, etc. The model lost grounding on which methods to call.

**Why isolated testing didn't catch it**: The F10 harness froze one reasoning and varied only artifacts. In that frozen context, the filter appeared to help (removing fabricated method names). In the full pipeline with varied reasoning, the filter removed too many valid references.

## Reverted Commits

These commits were reverted (code from commit E restored):

| Commit | What it did | Why reverted |
|--------|------------|--------------|
| `67032f5a` | Generic decision filter | -5.7 pts. Strips valid method refs from decisions. |
| `b97a9a2c` | F10 deterministic corrector | Net negative when combined. Replaces method names in artifacts, sometimes incorrectly. |
| `652dcb99` | Artifact_methods bypass fix | Part of corrector. |
| `428ac941` | F10 corrector detection gaps | Part of corrector. |
| `835e9fdc` | Import graph relative imports + chain completeness | Useful fix but bundled with filter. Needs re-application separately. |
| `ec34e778` | Cached class resolver | Useful fix but the repair functions it enables (attr-as-function, embed_batch) didn't improve scores. |
| `1d6e3428` | List-arg-to-batch repair | Part of class cache. |

## Preserved Commits (in current code)

| Commit | What it did | Score impact |
|--------|------------|-------------|
| `1bf4470f` | Best-of-3 scope consensus | +2.8 pts (run 66→67) |
| `b3d23b5d` | F10 compose rule + genericize prompts (F15-F18) | Neutral |
| `1f9549f1` | Split reasoning into design + roadmap_risk | -1.2 (within variance) |
| `ee09867d` | F20 decision merger (union-find) | Neutral |
| `17b50689` | Refinement pass (explore-then-focus) | Neutral |
| `15d726e6` | F12 strip `::` method suffix | Deterministic fix, neutral |

## Bug Fixes Applied on Top of E

Two bug fixes from later commits were cherry-picked onto commit E because they fix real bugs without architectural changes:

1. **Remove 8K truncation on roadmap_risk design summary** (from `8724f397`): The roadmap_risk reasoning call received `design_reasoning[:8000]` — truncating ~11K of design context to 8K. Caused 2/9 plans to have 0 roadmap phases.

2. **Add ADRs and risks to retry-if-empty fields** (from `85546e64`): Plans with 0 ADRs or 0 risks weren't retried. Only approaches, components, and phases had retry protection.

## Commits That Need Re-evaluation

These commits contain useful ideas but were bundled with the harmful filter/corrector and need separate re-implementation:

### Import graph relative imports (`835e9fdc`)
- **What**: `from .foo import X` now resolves in the import graph. Call graph gets edges between routes→service→engine.
- **Why useful**: The decomposition's `_build_coverage_hint` needs interior nodes to detect chain gaps.
- **How to re-apply**: Cherry-pick only `fitz_forge/planning/agent/indexer.py` changes (the `_extract_full_imports` relative import fix) and `fitz_forge/planning/pipeline/call_graph.py` (BFS cap 80→200). Do NOT re-apply the decision filter or corrector.
- **Risk**: Low — pure infrastructure fix, doesn't change what the LLM sees.

### Cached class resolver (`ec34e778`)
- **What**: Scans all .py files once, builds `{ClassName: ClassInfo}` map. Replaces 4 broken disk fallbacks that used filename heuristics.
- **Why useful**: Resolves classes like `DetectionOrchestrator` (in `registry.py`) that the filename filter missed. Enables `_build_attr_methods` to find 24 types vs 14.
- **How to re-apply**: Cherry-pick the `_ClassCache` class and migrate the 4 consumers. But do NOT re-apply the corrector that uses it (`_detect_fabricated_calls`, `_repair_fabricated_calls`).
- **Risk**: Medium — more types resolved means more repairs by `_repair_fabricated_refs`. Need to verify the existing repair (attr-as-function, fuzzy match) doesn't make wrong fixes with the expanded type map.

### F10 corrector concept
- **What**: AST-detect `object.method()` calls where method doesn't exist on the resolved type, replace with closest real method.
- **Why it failed**: The replacement is too aggressive. `difflib.get_close_matches(cutoff=0.0)` picks the "closest" method with NO minimum similarity. Also, the corrector replaces method names that might be intentionally new (the artifact is proposing to CREATE that method).
- **How to fix**: Instead of replacing fabricated methods, just LOG them as warnings. Let the model's output stand. The Sonnet scorer penalizes fabrication already — post-gen repair that makes wrong corrections is worse than leaving the fabrication.

## Key Lessons

1. **Isolated harness testing is necessary but not sufficient.** Every change tested positive in frozen-state harnesses (50+ runs). But combined effects in the full pipeline caused regression. Always run full pipeline benchmarks (8+ Sonnet-scored plans) after multiple changes.

2. **Filters that remove information from LLM prompts are dangerous.** The decision filter removed method names the model needed. The compose rule ("use existing methods") was fine because it ADDS an instruction. The filter was harmful because it REMOVES information.

3. **Post-generation repair can be net negative.** The deterministic corrector replaced fabricated method names, but sometimes replaced them with WRONG methods. A wrong method call is worse than a fabricated one — the fabricated one at least shows intent, while the wrong replacement is silently misleading.

4. **Bisecting with scored benchmarks is expensive but essential.** This bisect took ~12 hours (50+ plans, 50+ Sonnet scoring agents across 6 checkpoints). Without it, we would have kept adding fixes on top of a regression.

5. **Bug fixes are safe; architectural changes need scoring.** The truncation fix and retry-if-empty fix are pure bug corrections that improve structural completeness without changing what the LLM sees. These tested positive immediately. The decision filter and corrector changed what the LLM receives/produces — these needed full pipeline validation.

## Post-Bisect Work (2026-04-06)

### Run 67 rescore
Run 67's original 45.3 avg was inflated by Sonnet scoring variance. Cold rescore with fresh Sonnet subagents gave **40.9 avg**. The true baseline for this codebase+model is **~41**.

### Import graph fix re-applied (run 73)
Cherry-picked from `835e9fdc` onto commit I:
- `_extract_full_imports` resolves relative imports (`from .foo import X`)
- BFS cap 80→200 in call graph extraction
- Chain completeness rule added to decomposition prompt

**Result: 40.6 avg (5 plans, range 35-44) — neutral.** Expected: pure infrastructure fix, doesn't change what LLM sees.

| Plan | Files | Contract | Consistency | Alignment | Implement | Scope | Total |
|------|-------|----------|-------------|-----------|-----------|-------|-------|
| 73a | 6 | 8 | 5 | 5 | 6 | 8 | 38 |
| 73b | 8 | 7 | 6 | 6 | 7 | 8 | 42 |
| 73c | 7 | 6 | 5 | 5 | 5 | 7 | 35 |
| 73d | 9 | 7 | 6 | 7 | 6 | 9 | 44 |
| 73e | 7 | 9 | 7 | 7 | 6 | 8 | 44 |
| **avg** | **7.4** | **7.6** | **5.8** | **5.6** | **6.0** | **8.0** | **40.6** |

### Remaining reverted commits
1. ~~**Import graph fix**~~ — Done. Neutral. Committed.
2. **Cached class resolver** (`ec34e778`) — Not yet re-evaluated. Apply cache without corrector.
3. **F10 corrector concept** — Dead end for scores. Consider logging-only approach.

### F25 fix (2026-04-06, later in session)
Per-function artifact decomposition eliminated wrong field access in route artifacts:
- **Root cause**: `_extract_reference_method` picked `query()` (longest) as reference for BOTH `/query/stream` and `/chat/stream`. Model was told "follow this pattern exactly" with the wrong handler.
- **Fix**: `_decompose_multi_handler_artifacts` splits file-level artifacts into per-function artifacts. Each gets its correct reference handler.
- **Also fixed**: Pydantic field extraction in indexer, truncation preserves classes line, gatherer uses fitz_forge's own indexer, retry on ALL violation kinds.
- **Result**: wrong_field violations 83% → 0% (run 74 → run 77). Scoring run 77 in progress.

### Current priorities
- Scoring run 77 to measure overall impact on plan quality
- **Weakest dimensions** (from run 73): consistency (5.8) and alignment (5.6)
- Consistency failures: duplicate decision IDs, wrong critical paths, type mismatches across artifacts
- Alignment failures: ~~wrong field names~~ (FIXED by F25), fabricated attributes (self._chat_provider), wrong service paths
- Cached class resolver (`ec34e778`) — still not re-evaluated
- F10 corrector concept — dead end for scores
