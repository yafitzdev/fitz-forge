# V2-F7: Missing Required File — Decision→Synthesis Closure

> **Status:** Designed, not yet implemented. 2026-04-10.
> Related: `docs/v2-scoring/V2-F7-missing-required-file.md` (original failure
> pattern), `docs/roadmap/artifact-closure-principle.md` (same invariant
> shape at a different pipeline level).

## Problem

3/7 run 93 plans are missing `engine.py` from the final artifact set, plus
1 total reasoning failure (plan 04 — only 2 artifacts, no required files).
The deterministic score dropped from 92.2 (run 92) to 87.7 (run 93) almost
entirely because of V2-F7 variance, not because of closure regressions.

## Three distinct failure modes

**Mode 1: Synthesis drops referenced files** (plans 01, 04 in run 93)

The decomposition stage cites `engine.py` in 2+ decisions (plan 01's d8
explicitly says "`FitzKragEngine.answer()` does NOT expose a streaming
variant"), but the synthesis reasoning's `needed_artifacts` list omits it.
The model picks a workaround architecture (e.g. "add `stream_answer` on
`FitzService` that calls `create_engine()` directly, bypassing the
engine pipeline"). This is an *architectural reasoning failure*, not a
bug in extraction — the model deliberately chose to skip the engine.

**Mode 2: Decomposition blindness** (plan 03)

Decomposition produces 13 decisions, **zero** reference `engine.py`. The
model chose a flat route→service shortcut architecture during decomposition
itself. Synthesis can't save what decomposition never saw.

**Mode 3: Total reasoning failure** (plan 04)

Only 2 artifacts total, neither is a required file
(`core/answer.py`, `api/error_handlers.py`). Something earlier in the
pipeline broke completely.

## Why closure doesn't fully rescue Mode 1

Plan 01's repair chain *almost* works but breaks at the last link:

```
route → service.stream_answer()                  ← closure catches, adds fitz_service.py
          ↓
   fitz_service.stream_answer() (repair):
     engine_instance = create_engine(engine)     ← create_engine returns factory(config)
                                                    dynamic registry → inference returns None
     engine_instance.answer_stream(...)          ← closure CAN'T see this is a FitzKragEngine
                                                    call, so no engine.py expansion
```

The break is at `create_engine` — it returns `factory(config)` via a runtime
registry. Even docstring parsing fails: the docstring says *"Returns: Engine
instance implementing KnowledgeEngine protocol"*, and `KnowledgeEngine` is a
Protocol, not the concrete `FitzKragEngine`. Static AST inference can't
resolve this reliably without Protocol-to-concrete resolution.

## The real invariant

> **Decomposition→Synthesis closure:** every `.py` file referenced in 2+
> decision evidence entries must appear in `needed_artifacts`, OR the
> synthesis reasoning must explicitly justify omitting it.

This is a **set-level invariant on a pipeline transition**, not an
artifact-level invariant. Same shape as the closure family in
`closure.py`, one level up.

It's the same family of thinking that produced the artifact closure
principle: state the invariant the pipeline must satisfy, then enforce
it — rather than patching each specific symptom as it appears.

## Proposed fixes, ranked

### Fix A — Deterministic post-extraction injection

**Covers:** Mode 1
**Effort:** ~1h
**Recommendation:** do this first

After `_extract_needed_artifacts()` returns in synthesis:

```python
def _enforce_decision_coverage(
    needed_artifacts: list[str],
    resolutions: list[dict],
) -> list[str]:
    """Auto-inject files referenced in decisions but missing from needed_artifacts.

    Enforces the invariant: every file cited in 2+ decision evidence entries
    must appear in needed_artifacts. Uses a derived purpose from the decision
    text that mentions the file most prominently.
    """
    from collections import Counter

    file_refs: Counter[str] = Counter()
    file_to_decisions: dict[str, list[dict]] = {}

    for r in resolutions:
        for ev in r.get("evidence", []):
            if ":" not in ev:
                continue
            fname = ev.split(":")[0].strip()
            if not fname.endswith(".py"):
                continue
            file_refs[fname] += 1
            file_to_decisions.setdefault(fname, []).append(r)

    existing = {a.split(" -- ")[0].strip() for a in needed_artifacts}
    for fname, count in file_refs.items():
        if count < 2 or fname in existing:
            continue
        # Derive purpose from the most-detailed decision that mentions this file
        best_decision = max(
            file_to_decisions[fname],
            key=lambda r: len(r.get("decision", "")),
        )
        purpose = (
            best_decision.get("decision", "")[:200]
            .replace("\n", " ")
            .strip()
        )
        needed_artifacts.append(f"{fname} -- {purpose}")
        logger.info(
            f"V2-F7: auto-injecting {fname} ({count} decision refs)"
        )

    return needed_artifacts
```

**Pros:**
- Simple, deterministic, codebase-agnostic
- Directly matches decomposition's own conclusions
- No prompt changes, no model-dependent behavior
- Matches the closure-principle framing (invariant over pipeline transition)

**Cons:**
- Could over-inject if a decision analyzed a file but concluded "no changes"
  needed. Mitigation: the black box's per-artifact validation will skip
  unnecessary surgical rewrites; worst case is a ~0-cost artifact generation
  for a file that didn't need changes.

**Impact on run 93:** Plans 01 and 04 would get `engine.py` auto-injected,
lifting completeness from 15/30 to 30/30 on each.

### Fix B — Decomposition-level coverage gate

**Covers:** Mode 2
**Effort:** ~2-3h
**Recommendation:** only if Fix A alone doesn't move the needle

Add a coverage criterion to the decomposition scorer that rejects candidates
where the task's verb + noun phrases reference a file in the structural
index that no decision mentions.

Sketch:
- Parse the task text for (verb, noun) pairs: "add *streaming*", "extend
  *answer*", etc.
- For each noun, search the structural index for classes/methods containing
  that noun
- If a high-confidence match exists (e.g. `FitzKragEngine.answer()` matches
  "extend answer") but no decision evidence cites that class's file, mark
  the candidate as low-coverage
- Retry decomposition up to N times; on exhaustion, emit a warning and
  proceed

**Pros:** addresses Mode 2 root cause
**Cons:** fuzzy noun-phrase matching, risk of bogus retries, task-semantic
rather than invariant-structural

### Fix C — Closure transitive inference for Protocol→concrete resolution

**Covers:** the tail of Mode 1 that slips past Fix A (when the model writes
a repair artifact whose body references an engine method)
**Effort:** ~2h
**Recommendation:** optional follow-up to Fix A

In `grounding/inference.py`, extend docstring parsing:
- Parse *all* capitalized identifiers in the `Returns:` section, not just
  the first one
- For each candidate, check if it's a known class
- If the candidate is a Protocol (detectable via `bases` or `@runtime_checkable`),
  try to find the concrete class in the index that implements it and use
  that instead

The existing docstring regex already handles the common case
(`Returns: ClassName ...`). This extension handles
`Returns: Engine instance implementing KnowledgeEngine protocol`.

**Pros:** makes closure's repair loop more robust
**Cons:** Protocol-to-concrete matching is fuzzy; picking the "right" concrete
impl when there are multiple is ambiguous

### Fix D — Architectural template gate

**Covers:** streaming-specific task pattern
**Effort:** ~3h
**Recommendation:** **skip** — too task-specific

A post-synthesis check that any plan touching streaming routes must also
modify the engine OR explicitly justify the omission in its reasoning.
Doesn't generalize beyond streaming tasks.

## Recommendation

**Phase 1:** Implement **Fix A** alone. It's the direct expression of the
invariant, costs ~1h, and addresses the most common failure mode
deterministically.

**Phase 2 (after benchmarking Fix A):** If V2-F7 still shows up in Mode 1
cases where the decomp-reference signal is weaker (1 reference instead of 2),
add **Fix C** to close transitive fabrications in repair artifacts.

**Phase 3 (if Mode 2 is still frequent):** Tackle **Fix B**. This is a
harder design problem and should only be attempted once we've exhausted the
easier wins.

**Never:** Fix D.

## Impact prediction

With **Fix A alone**, in a run 93 rerun:
- Plans 01, 04: `engine.py` auto-injected → completeness 15/30 → 30/30 → +15pt each
- Plan 03: no change (Mode 2, unaffected by A)
- Other plans: unchanged

Deterministic avg: 87.7 → **~91-93**, back into run 92 territory while
keeping closure's R3 improvement.

Combined (deterministic + taxonomy): 72.4 → **~78-82**, above run 92's 76.7.

Tier 2 taxonomy: Fix A alone doesn't guarantee better taxonomy scores
because Sonnet may still flag semantic issues inside the newly-injected
engine.py. The closure family's usage checks should catch the worst of
these (async/sync mismatch, wrong return types).

## Notes on invariant-driven placement

Fix A is the same architectural pattern as the artifact closure principle:

| | Artifact closure | V2-F7 fix |
|---|---|---|
| Invariant | "every cross-file reference is satisfied" | "every file decomp cares about is planned" |
| Scope | set of artifacts | set of (decisions, needed_artifacts) |
| Enforcement | `check_closure` + repair loop | `_enforce_decision_coverage` injection |
| Level | within black box (batch entry) | decomp→synthesis pipeline transition |

Both belong in the code path that owns that transition. Artifact closure
lives in `generate_artifact_set`. Decision→synthesis closure belongs in
synthesis stage's `_build_artifacts_per_file` just before it consumes
`needed_artifacts` — or cleaner, inside the synthesis field-extraction
logic where `needed_artifacts` is first produced.

## Related

- `docs/v2-scoring/V2-F7-missing-required-file.md` — the original failure pattern doc (keep, reference)
- `docs/roadmap/artifact-closure-principle.md` — the sibling closure invariant (same shape, different level)
- CLAUDE.md rule 10 (fix invariants, not symptoms) — this doc is a direct application
- CLAUDE.md rule 11 (watch for set-level bugs) — Fix A catches a set-level bug between pipeline stages
