# Ranking Explanations — Bug Register

Task: "Add query result ranking explanations so users can see why each
source was ranked in its position — showing the retrieval method used,
relevance signals, and reranking rationale for each source in the response"

Taxonomy: `benchmarks/ranking_explanations/taxonomy.json`
Context: `benchmarks/ranking_explanations/ideal_context.json`

## Run 022 Baseline (10 plans)

| Metric | Value |
|--------|-------|
| Plans | 10 |
| Average | **68.85** |
| Artifact quality | 50.0/50 on all 10 (perfect) |
| Completeness | 0.0-3.8/30 (terrible) |
| Consistency | 10-20/20 |
| engine.py present | 0/10 |
| ranker.py present | 0/10 |
| reranker.py present | 0/10 |

Key observation: **artifact quality is flawless** — the overnight Fix I / B1-B7
work is fully task-agnostic. The problem is purely completeness (the synthesis
stage picks the wrong files to generate artifacts for).

## Open

### B4-rank — Consistency: false-positive `method_name_agreement` on container/router names
- **Impact:** 7
- **Evidence:** Ranking replay — `synthesizer.py calls provenance.append()` matched against `provenance.py` (list method, not provenance method). `query.py calls router.post()` matched against `router.py` (FastAPI APIRouter method, not RetrievalRouter method). Variable names collide with file names.
- **Generalization:** the consistency check matches variable-name → file-basename, which produces false positives when common names (`router`, `provenance`, `response`) appear in both contexts.
- **Fix needed:** skip method_name_agreement when the called method is a stdlib/framework method (append, extend, post, get, put, delete, etc.)

### B1-rank — Synthesis drops required files despite resolved decisions
- **Impact:** 10
- **Evidence:** All 10 plans have decisions d6 (ranker.py) and d7 (reranker.py)
  with concrete evidence. Resolution produces valid decisions. But synthesis
  reasoning selects only strategy-level files (types.py, section_search.py,
  code_search.py, table_search.py) and API-layer files. Ranker, reranker,
  and engine are never generated as artifacts.
- **Root cause:** synthesis reasoning builds the file list from its own
  assessment of "what needs code changes." It picks strategy files (where
  Address.metadata gets populated) and API files (where it's exposed), but
  considers ranker/reranker as "orchestration that doesn't need new code."
  V2-F7 injection (backup) requires 2+ evidence citations per file —
  ranker.py and reranker.py each appear in only 1 decision's evidence,
  so injection doesn't fire.
- **Generalization:** the invariant is "every resolved decision targeting
  a specific file should produce an artifact for that file." Currently
  there's no enforcement. Synthesis can silently drop decided files.
- **Fix options (ranked by safety):**
  1. **(safest)** After synthesis selects its files, scan resolved decisions
     for files NOT in the selection. If a decision explicitly targets file X
     with evidence, auto-inject X into `needed_artifacts`. This is the
     same shape as V2-F7 but driven by decision→file mapping instead of
     cross-citation count.
  2. Lower V2-F7 threshold from 2 to 1 — risky, would inject every file
     referenced by any decision.
  3. Prompt-level: tell synthesis reasoning "you must include one artifact
     per resolved decision" — unreliable LLM instruction.
- **Regression risk for streaming:** LOW. Fix option 1 would add files the
  model's decisions already identify. For streaming, decisions already
  target engine.py and routes/query.py (which are already included). The
  fix would be additive — more files included, never fewer. Artifact
  quality shouldn't regress because closure/grounding will catch bad
  artifacts.

### B2-rank — Consistency: `method_name_agreement` failures (4/10 plans)
- **Impact:** 5
- **Evidence:** Plans 02, 05, 06, 08 have `method_name_agreement` consistency
  failures. Likely: one artifact calls a method that another artifact defines
  with a different name (e.g., `get_ranking_explanation` vs
  `get_explanation`).
- **Root cause:** synthesis generates artifacts independently per-file; if
  two files reference the same concept with different names, the cross-
  artifact check catches the mismatch.
- **Fix:** already addressed by closure repair loop (strategy 2 regenerates
  with sibling feedback). May resolve naturally once B1-rank is fixed
  (more artifacts = better cross-artifact consistency signal).

### B3-rank — Plans 03, 07 have zero completeness (no API-layer artifacts)
- **Impact:** 3
- **Evidence:** These plans generate only types.py + strategy files (4-5
  artifacts). No schemas.py, no query.py, no synthesizer.py. Synthesis
  reasoning decides the task is "internal retrieval plumbing" and skips
  the API surface entirely.
- **Fix:** same as B1-rank (decision-driven injection would add API files
  referenced in decisions d8, d9).

## Resolved

### B1-rank — Synthesis drops required files (evidence-source injection)
- Closed 2026-04-16. See `synthesis.py:_enforce_decision_coverage` criterion 2.
  Files appearing as evidence sources in resolved decisions are now injected
  even if only referenced by 1 decision. ranker.py and reranker.py now included.

### B2-rank-parse — Import-split parse recovery
- Closed 2026-04-16. See `inference.py:try_parse` recovery step 4. When the
  model outputs `import X` at indent 0 followed by `def method(self,...)` at
  indent 4, split the imports and class-wrap only the body. ranker.py and
  reranker.py now parse successfully.

### B3-rank-container — Container-type false positives (`list[Foo]` typed as `Foo`)
- Closed 2026-04-16. See `inference.py:extract_type_name` + `_CONTAINER_TYPES`.
  `list[X]`, `dict[K,V]`, `set[X]` etc. now return the container name (which
  falls through _SKIP_NAMES), not the element type. Prevents `items.append()`
  from being flagged as `Foo.append()`.

## Progress

| Cycle | Fix | Plan 01 replay | Notes |
|---|---|---|---|
| Baseline (run 022) | — | 73.8 | comp=3.8, art=50, cons=20 |
| +B1-rank (injection only) | evidence-source | 63.2 | comp=5.2 but ranker/reranker parse-fail; new closure FPs |
| +B2-rank +B3-rank (parse+container) | import-split + container | **75.5** | comp=20.0, art=48.8, cons=6.7. 11/11 artifacts succeed. Consistency drops from name-collision FPs |
| +B4-rank (consistency scorer) | stdlib method skip | **94.4** | comp=25.3, art=49.1, cons=20.0. Consistency fully restored. |
| Fresh 10-plan (run 023) | all fixes | **97.08 avg** | Min 90.0, Max 100.0, 0 below 90. **+28.23 from baseline.** |

## Regression Analysis

| Fix | Streaming impact | Risk |
|-----|-----------------|------|
| B1-rank (decision-driven artifact injection) | Additive only — streaming decisions already target the right files; injection adds safety net | LOW |
| B2-rank (consistency improvement) | No code change needed — may resolve with B1-rank | NONE |
| B3-rank (same as B1-rank) | Covered by B1-rank | LOW |

**Conclusion:** The streaming benchmark (run 021, avg 98.35) should NOT
regress from ranking-task fixes because the root cause is completeness
(which files are selected), not quality (how artifacts are generated).
The quality layer (closure, grounding, validation) is already proven
task-agnostic at 50/50 across both benchmarks.
