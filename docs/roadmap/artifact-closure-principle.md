# Artifact Set Closure Principle

> **Status:** Implemented 2026-04-10. See `fitz_forge/planning/artifact/closure.py`
> and `generate_artifact_set()` in `generator.py`. The check enforces five
> invariants: existence (closure proper), usage (async/sync), kwargs, imports,
> and field access. Replay-verified: plan 08 goes from 2 R3 violations to a
> closed set in 1 repair iteration, with the regeneration loop catching the
> residual async/sync mismatch.



## Problem

The artifact black box currently validates artifacts **one at a time**. Every per-artifact check (parseable, yield, return type, fabrication) looks at the artifact in isolation against the codebase's structural index. This is sufficient for catching intra-file bugs but cannot catch bugs that only exist at the **set** level.

### Observed failures (run 92, Sonnet Tier 2 classification)

All 10 plans passed per-artifact validation, yet **10/10 routes were classified R3** (calls to non-existent service-layer methods). Concrete examples:

1. **Fabricated cross-file reference** — plan 08's route calls `service.query_stream(...)`, but the plan never includes a `services/fitz_service.py` artifact that adds `query_stream` to `FitzService`. The current fabrication check doesn't catch it because the checker can't infer the type of the local variable `service = get_service()`.

2. **Sibling-to-sibling inconsistency** — within plan 08, the SDK artifact (`sdk/fitz.py`) calls `self._service.answer_stream(...)` while the route artifact (`routes/query.py`) calls `service.query_stream(...)`. Two sibling artifacts invent **two different names** for the same intended method. Per-artifact validation can never catch this because each artifact is consistent in isolation — you have to look at the set to see they disagree.

3. **Missing downstream artifact** — plan 06's engine calls `self._synthesizer.generate_streaming(...)`, but no synthesizer artifact defines `generate_streaming`. Same pattern, different layer: the symbol is referenced but never provided.

All three have the same shape: **an artifact references a symbol that neither exists in the codebase nor is provided by a sibling artifact in the same plan.** The current validator has no way to express this because it only sees one artifact at a time.

## Principle

**Closure invariant:** For every plan, the set of generated artifacts must be *closed* — every cross-file symbol referenced by any artifact must be satisfied either by

- the existing codebase (verified via structural index + source-dir augmentation), **or**
- a sibling artifact in the same set that defines/extends that symbol.

If closure fails, the plan is not implementable as-is, regardless of how clean each individual artifact looks.

This is a property of the **set**, not of any individual artifact. No amount of per-artifact validation can enforce it.

## Design

### Interface change: per-artifact → batch

Today's black box entry point:

```python
generate_artifact(filename, purpose, ctx) -> ArtifactResult
```

becomes (or is wrapped by):

```python
generate_artifact_set(needed_artifacts, ctx) -> list[ArtifactResult]
```

Synthesis stage calls `generate_artifact_set` once with the full `needed_artifacts` list. It receives back a closed, implementable set or an explicit failure. Synthesis does not learn anything about closure — the invariant is fully owned by the black box.

The per-artifact generator stays unchanged and is reused internally.

### Internal loop

```
1. Generate each artifact (existing per-artifact black box)
2. Extract referenced symbols from every generated artifact
3. Extract provided symbols (codebase ∪ definitions in sibling artifacts)
4. Compute: missing = referenced - provided
5. If missing is empty → return clean set, done
6. Else:
    a. If a missing symbol clearly belongs in a new file (e.g. FitzService method →
       services/fitz_service.py), add that file to needed_artifacts and regenerate it
    b. Otherwise, retry the violating artifact with feedback:
       "Symbol X doesn't exist and no sibling artifact provides it.
        Either use an existing method or the plan must include an artifact adding it."
7. Cap total repair iterations (e.g. 2) to bound cost
```

### Dependency extraction

For each artifact, extract:

- **Referenced symbols** — cross-file method calls, imported types, class references. Reuse the existing `grounding.check_artifact` AST pass, but emit the full reference list rather than only violations. Needs light type tracking for locals like `service = get_service()` → `FitzService` so that `service.X()` becomes `FitzService.X`.
- **Provided symbols** — top-level `def`s, `class`es, and methods added to existing classes by a surgical rewrite. This is what other siblings are allowed to rely on.

The checker already has most of this infrastructure. The new work is:
- Type tracking for a small set of service-locator patterns (`get_service`, `get_X`-style factories)
- A `provides()` function per artifact to symmetrize `references()`
- The set-level closure loop

### Repair strategies

Two strategies for a closure violation:

1. **Expand the set** — add a new artifact that supplies the missing symbol. Best when the missing symbol clearly belongs in a specific file (e.g. a fabricated `FitzService.X` → add `services/fitz_service.py` artifact with surgical rewrite adding `X`). Requires a small routing heuristic: given a missing symbol, what file should own it? For symbols called on known types, the file is the class's definition file.

2. **Regenerate the violator** — retry the artifact that made the bad reference, with an error message telling the model to remove or replace the symbol. Best when the reference was gratuitous (e.g. the model could have used an existing method).

Default: try strategy 1 first (expand), fall back to strategy 2 (regenerate) if expansion fails or isn't routable.

### Failure mode

If repair doesn't converge within the iteration cap, return an `ArtifactSetResult` with `closed=False` and the list of still-missing symbols. Synthesis can decide whether to accept a non-closed set (marked as such in the plan output) or to fail the stage. For benchmarking, a non-closed set should count as a failed plan regardless of individual artifact scores.

## What this does NOT change

- The per-artifact black box stays the same. Strategies, validation, retry loops, raw code output — all unchanged.
- Synthesis reasoning prompts stay the same. No new instructions, no decision-level changes.
- The structural index and source-dir augmentation stay the same.
- `ArtifactContext` is unchanged (maybe gains a `sibling_signatures` field for mid-loop feedback, but that's additive).

The only architectural change is at the **entry point** of the black box: it becomes batch-oriented.

## Why this is the right fix

Previous attempts to fix cross-file fabrication (e.g. `V2-F8b` for fabricated provider subclasses, `V2-F7` for missing required files) were all **symptom-driven**: each patch targeted a specific failure mode without stating the underlying invariant. They worked for one case and missed the next variant.

The closure principle is **invariant-driven**. Any future fabrication pattern that violates the invariant gets caught by the same check, without adding new rules. Concretely:

| Observed bug | Current fix | Closure catches it |
|---|---|---|
| Fabricated route → service method | No fix | Yes (missing provided symbol) |
| Fabricated engine → synthesizer method | No fix | Yes |
| SDK and route disagreeing on method name | No fix | Yes (both reference a non-existent symbol, of differing names) |
| Missing engine.py when routes call engine-level things | Partial (V2-F7 decomp scorer) | Yes (closure at pipeline level) |
| Fabricated request DTOs (V2-F8c) | Decomp scorer ref_complete | Yes (subsumed) |
| Fabricated provider subclasses (V2-F8b) | Decomp scorer ref_complete | Yes (subsumed) |

Several existing patches become redundant once closure is enforced.

## Effort

- Dependency extraction with light type tracking: ~half a day
- Closure loop + repair strategies: ~half a day
- Routing heuristic for strategy 1: ~2-3h
- Integration into synthesis stage and benchmarking: ~2-3h
- Tests: ~half a day

Total: 2-3 days end-to-end. More than a point patch, but it eliminates an entire class of bugs rather than just the currently-visible variant.

## Success criterion

After implementation, run the standard 10-plan benchmark. Tier 2 Sonnet classification should show ≤1/10 plans with R3 (fabricated service methods), down from 10/10 in run 92. The combined (deterministic + taxonomy) score should close the gap: run 92 combined avg was 76.7 vs deterministic 92.2 — most of that 15.5-point gap is R3. After closure, the gap should shrink to single digits.
