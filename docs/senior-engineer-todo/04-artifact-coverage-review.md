# Artifact-coverage review — senior-engineer catch for dropped user-intent files

**Status:** landed 2026-04-19
**Source:** 2026-04-19 run_049 plan_03 postmortem (missing synthesizer.py)
**Landed in:** `fitz_forge/planning/reviews/artifact_coverage.py`
(deterministic set-difference review) + `SynthesisStage.
_artifact_coverage_review_pass` which regenerates missing files via
`_build_missing_artifacts` (shared `_clean_artifact_specs` filter
with `_build_artifacts_per_file`). Tests in
`tests/unit/test_reviews_artifact_coverage.py` (13) and
`tests/unit/test_synthesis_artifact_coverage_wiring.py` (5).

## Original spec (below) kept for reference

## What a senior engineer would say

"Your context section's `needed_artifacts` lists synthesizer.py and calls
out 'update logic to propagate Address rationale into Provenance objects.'
I don't see synthesizer.py in your artifact set. Did you intentionally
drop it, or did you lose track?"

Today nothing in the pipeline asks that question. The context extraction
records the intent, the per-file generator produces what fits within its
budget, and the plan ships without either delivering the file or
flagging the gap. A reader of the plan has no way to tell the difference
between "we evaluated synthesizer.py and concluded no change was needed"
and "we silently dropped it."

## Evidence

Run_049 plan_03 (A2, 72.5):

- `context.needed_artifacts` contained
  `fitz_sage/engines/fitz_krag/generation/synthesizer.py -- update logic
  to propagate Address rationale into Provenance objects`
- `design.artifacts` had 11 files, synthesizer.py was not one of them
- Taxonomy scored synthesizer.py as `S3` (absent), -20 pts on the plan

Root cause of this specific instance: `_build_artifacts_per_file`'s
`[:12]` cap evicts original `needed_artifacts` entries after
`_enforce_decision_coverage` injects extras. The structural fix is a
two-pass `artifact_specs` build (user-intent first, coverage-injected
fills remainder).

But the structural fix is not the point of this TODO. The point is:
**this bug slipped through because no runtime check enforced the
invariant "every `needed_artifact` either ships as an artifact or is
explicitly marked as not-needed-after-all."** Future variants will
slip through the same gap.

## The invariant (set-level)

`set(f for f in context.needed_artifacts) ⊆ set(a.filename for a in
design.artifacts) ∪ set(explicitly_dropped_with_reason)`

This is a set property, not a per-item check. Rule 11 applies — one-at-
a-time validation will never catch it. Rule 12 also applies — B1
("synthesis drops required files") was the same family, addressed with
a targeted Python fix; this would be the second instance. Before a
third one slips through we should install the review rather than keep
patching individual eviction paths.

## The review

A new sibling module `fitz_forge/planning/reviews/artifact_coverage.py`
with the same `ReviewResult` / `ReviewIssue` shape as the other
reviews. Runs after design is finalized and before the plan is
rendered. Inputs:

- `context.needed_artifacts` (list of `path -- purpose` strings)
- `design.artifacts` (list of `{filename, content, purpose}`)
- Resolved decision evidence (to distinguish deliberate drops from
  accidental ones)

Output: one `ReviewIssue` per file in needed_artifacts whose filename
is not present in design.artifacts. `actual` explains what the context
said; `suggestion` tells the downstream coder either to regenerate
that specific artifact or to document why it was intentionally
dropped.

Cheap first cut: deterministic set difference. It's a perfect fit for
a Python check — no LLM needed, runs in milliseconds. Later, if
decisions-drop-a-file-on-purpose becomes common, an LLM pass can judge
whether the drop was justified.

## Regeneration path

When the review flags missing files, route each missing file back
into `_build_artifacts_per_file` with its purpose string. The
generator already handles per-file artifact generation; the review
just needs to hand it the list of survivors that didn't make it.

This is cheaper than re-running the whole synthesis.

## Guardrails required

- **Deterministic first, LLM later.** Start with the set-difference
  check. Don't spend an LLM call on what a `set.difference()` can
  answer.
- **Bounded regen.** Max one pass of "generate missing artifacts."
  If the generator still drops files, surface them as
  `review_findings` and move on — don't loop.
- **Explicit drop protocol.** If a decision resolution marks a file
  as "not needed after all," the context extraction should remove it
  from needed_artifacts. The review treats anything remaining in
  needed_artifacts as committed intent.

## Cost

- Deterministic check: negligible.
- Regeneration (when missing): +1 `_build_artifacts_per_file` call per
  missing file (~10-20s each).
- Only fires on actual drops — clean plans pay nothing.

## Expected lift

Direct lift: +20 taxonomy points on any plan whose scoring file gets
dropped (synthesizer.py worth 20 in ranking, routes/query.py worth 20,
etc.).

Run_049: 1/5 plans had this issue. If the review catches + regenerates,
that plan climbs from 72.5 to ~90+ and the mean lifts to ~85.

Longer term, it prevents the same-shape bug from re-surfacing under
different eviction codepaths.

## Test strategy

Unit:

- Design with all needed_artifacts present → review passes.
- Design missing one needed_artifact filename → review flags one issue.
- Design missing multiple files → one issue per file.
- Context with no needed_artifacts → review skipped.

Replay:

- run_049 plan_03 snapshot → review fires, flags synthesizer.py,
  regeneration produces the missing artifact. Final plan contains
  synthesizer.py with propagation logic.

## Out of scope

- Dropping `needed_artifacts` entries in context extraction. That's a
  different problem and the review would catch its consequences
  anyway.
- Checking that `design.artifacts` covers every file referenced in a
  resolved decision's evidence. `_enforce_decision_coverage` already
  does this at the set-level; the review here is the safety net on
  top of it.

## Relationship to B1

B1 (already resolved) fixed the under-injection side of the
same-shape bug: "files with only one decision citation weren't being
injected into needed_artifacts." That was a Python fix to the
injector.

This TODO is the runtime-check counterpart: a senior-engineer review
that would catch any variant of "needed file didn't make it into the
final artifact set," regardless of which codepath dropped it.
