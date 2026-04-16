# V2 Bug Register

Live queue of failure patterns observed in benchmark runs. Each cycle:
1. Run replay (or benchmark) against the latest fixes
2. Add any new failure patterns here with an impact score
3. Pick the single highest-impact open bug, fix it (generalized to every variant)
4. Validate via replay
5. Mark done, loop

**Impact scale (1–10):** 10 = causes most plans to fail wholesale; 5 = blocks one file per plan; 1 = cosmetic.

## Open

### B8 — Scorer `type_agreement` over-flags same-name methods on different classes
- **Impact:** 3 (tooling, plan-quality signal)
- **Evidence:** Run 20 plan_02 — scorer reports `Streaming methods have incompatible return types` because `core/answer.py:stream_query -> Generator[AnswerChunk, None, Answer]` and `api/routes/query.py:stream_query -> Generator[str, None, None]`. These are on DIFFERENT classes at different layers (core streamer vs API wrapper) — the names match but the methods aren't the same concept.
- **Generalization:** type agreement check should group by owner class + method name, not just method name. Methods in different files/classes sharing a name are not necessarily the same concept.
- **Cost:** plan_02 cons 20 → 10, total 100 → 89.3. Single-plan impact only right now.

## Resolved

### R1 — Class fabrication only checked on `ClassName(...)` instantiation (Fix I)
- Closed 2026-04-16. See `closure.py:_iter_annotation_class_names`, `_find_module_typevars`. Generalized to every type position: param/return/variable annotations, raise, except, isinstance, cast, instantiation.

### R2 — `_strip_fences` ate leading indentation on multi-method surgical outputs
- Closed 2026-04-16. See `strategy.py:_strip_fences`. Now strips blank lines and fences only, preserving indentation on real code lines.

### R3 — Fix I false-positives single-letter TypeVars and `T = TypeVar("T")` bindings
- Closed 2026-04-16. See `closure.py:_find_module_typevars` + single-letter filter in `_iter_annotation_class_names`.

### B1 — `_check_empty` rejects class-only files (schemas.py, answer.py)
- Closed 2026-04-16. See `validate.py:_check_empty` + `_is_data_class`. Accepts BaseModel, dataclass, Enum, TypedDict, and any class with annotated fields. Cycle 3 replay: schemas.py and answer.py now succeed on attempt 1.

### B2 — engine.py fabricates `self._private_method` that doesn't exist
- Closed 2026-04-16. See `context.py:_extract_target_self_methods` + `strategy.py:_surgical_grounding_block`. The real method signatures of the target class are now injected as a `METHODS AVAILABLE ON self` block with an explicit "do NOT invent new helper names" rule in both surgical and new_code prompts.
- Cycle 2 replay result: engine.py succeeds on attempt 1 (16213 chars, 1 sig) — no retries. **Plan 01 deterministic score 87.9 → 100.0** after this cycle + B1 + Fix I stack.

### B3 — Grounding AST parser rejects surgical artifacts (no class-wrap fallback)
- Closed 2026-04-16. See `grounding/check.py`. Both `check_artifact` and `_check_parallel_signatures` now call `try_parse` from `grounding/inference.py` so class-wrapped surgical output parses consistently with closure.
- Cycle 4 replay: no more `parse_error` on `engine.py` or `fitz_service.py` — grounding now validates them end-to-end.

### B4 — Grounding false-positive "missing class" for real classes (ChatResponse, Answer, Chunk, …)
- Closed 2026-04-16. See `grounding/check.py:check_all_artifacts` + `grounding/llm.py:validate_grounding` + `orchestrator.py`. `source_dir` is now threaded through so grounding calls `StructuralIndexLookup.augment_from_source_dir` with the full codebase, matching what closure does.
- Cycle 4 replay: grounding violations 4 → 1. The remaining 1 is a real `param_mismatch` (stream_query parallel sig), not a false positive.

### B5 — Replay scorer runs on original plans, not `plan_replay.json`
- Closed 2026-04-16. See `benchmarks/plan_factory.py:replay_cmd`. After the batch scoring, the command now loads `plan_replay.json` and prints a dedicated `=== REPLAY DETERMINISTIC SCORE ===` block so every cycle has immediate A/B measurement without manual scripting.

### B6 — Protocol widening (`ChatProvider.chat_stream` when the method is on `StreamingChatProvider`)
- Closed 2026-04-16. See `closure.py:_owner_is_protocol` + `_method_exists_anywhere` + the updated `_ref_in_codebase`. When the declared owner is a `Protocol` and the method exists on any class in the codebase, the call is accepted as protocol widening / duck-typing.
- Run 20 rescored: 4/5 previous violations cleared.

### B7 — `Enum.value` flagged as missing field
- Closed 2026-04-16. See `closure.py:_ENUM_STANDARD_ATTRS` + `_is_enum_class`. Enum subclasses walking to an `Enum`/`Flag`/`IntEnum`/`StrEnum`/`IntFlag`/`ReprEnum` base now accept standard Enum attributes (`value`, `name`, `_value_`, `_name_`, `_ignore_`, `_order_`, `_missing_`).

### Scorer consistency check missing source_dir augmentation
- Closed 2026-04-16. See `benchmarks/eval_v2_deterministic.py:check_cross_artifact_consistency` + `run_deterministic_checks`. `source_dir` is now threaded through so the consistency check's codebase-method skip list is full-codebase, not the truncated retrieval subset. Scorer correctness bug that was masking the closure fixes — fresh 5-plan run 20 re-score: 93.10 → **97.78**.

## Cycle results

| Cycle | Fix stack | Plan 01 Tier 1 | Notes |
|---|---|---|---|
| 0 (baseline) | — | 87.9 | Completeness 30, Artifacts 47.9, Consistency 10 |
| 1 | Fix I + strip_fences + TypeVar | unclosed loop | fitz_service.py repair now parses; closure reaches closure via 2 repair iters |
| 2 | +B1 +B2 | **100.0** | All 5 artifacts success on attempt 1; all three sub-scores maxed |
| 3 | +B3 +B4 | **100.0** | Plan 01 stable at 100; grounding FPs drop from 4 → 1; no more parse_error |
| 4 | +B5 | **100.0** | Tooling: replay now prints its own score |

Plan 01 went 87.9 → 100.0 (+12.1). Plans 02, 03 were already 100.0 on run 19. Plan 04 (94.8) and 05 (99.1) not yet replayed but should inherit Fix I / B1-B5 gains automatically in fresh runs.

## 30-Run Consistency Gate (Run 021)

| Metric | Value |
|--------|-------|
| Plans | 30 |
| **Average** | **98.35** |
| Median | 99.4 |
| Min | 89.5 |
| Max | 100.0 |
| Std dev | 2.96 |
| Perfect 100s | 6 (20%) |
| >= 95 | 27 (90%) |
| >= 90 | 28 (93%) |
| < 90 | 2 (7%) — plans 21 (89.5) and 23 (89.9) |

Both sub-90 plans have near-perfect artifacts (49.5, 49.9/50) with a single
consistency failure (cons=10/20) — the B8 type_agreement false positive.
Fixing B8 would push them to ~99.5.

**Gate status: PASSED.** Average 98.35 >> 95 target. 2 duds out of 30 (user
budgeted 1 per 10 = 3 per 30, we're under budget).

## Score progression

| Cycle | Fix stack | Plan 01 Tier 1 | Notes |
|---|---|---|---|
| Baseline (run 19 original) | — | 87.9 | Completeness 30, Artifacts 47.9, Consistency 10 |
| After Fix I + strip_fences + TypeVar | R1, R2, R3 | ~91 (estimated, repair loop) | fitz_service.py repair now parses; closure reaches closure |
| After B1 + B2 | +B1, +B2 | **100.0** | Completeness 30, Artifacts 50, Consistency 20. All maxed. |

Plans 02, 03 were already 100.0 on original run 19. Plan 04 (94.8) and plan 05 (99.1) not yet replayed.
