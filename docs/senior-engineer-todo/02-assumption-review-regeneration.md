# Assumption review — upgrade from surface-findings to context regeneration

**Status:** open
**Source:** 2026-04-19 MVP landing
**Blocks:** assumption-wrong plans silently building on contradicted premises

## What works today

The assumption review (`reviews/assumptions.py`) fires after the
context section assembles and the model has recorded its uncertainty
list. When an assumption is contradicted by the codebase or is
high-impact unverifiable, the review emits a `ReviewIssue` with a
specific file/class reference in `actual`.

The MVP **attaches findings to `context.review_findings`** in the plan
output. A downstream coder (human or agent) reads them, sees which
assumptions to double-check, and can adjust accordingly. Strictly
additive — no regeneration, no risk of making the plan worse.

## What doesn't work

The plan itself is still built on the (possibly wrong) assumptions.
Downstream architecture / design / roadmap decisions inherit the
defect. If an assumption is demonstrably contradicted by the code, the
plan has built on a false premise and everything after it is suspect.

## The fix

Two-step regeneration when contradictions are found:

1. **Rewrite the affected assumptions.** Call `generate()` with a
   prompt that shows the original assumptions + the review's findings
   + the codebase evidence, and asks for a corrected assumption list.
   Validate via `Assumption` schema.
2. **Re-extract the context section.** The other context fields
   (requirements, constraints, scope_boundaries) may also need
   revision because they were written assuming the wrong premise.
   Append a "corrected assumptions" block to the synthesis reasoning
   and re-run `_CONTEXT_FIELD_GROUPS` extraction.

Unlike the architecture case, the assumption fix is cheaper — most
assumptions are local to the context section; downstream stages
(architecture, design) should consume the corrected context through
normal prior_outputs flow.

Shape:

```python
# in _senior_assumption_review_pass when review.issues contain
# contradicted cases:
feedback = format_issues_feedback(review.issues)
retry_messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": (
        f"Correct these assumptions in light of the review:\n\n"
        f"Original assumptions: {assumptions}\n\n"
        f"Senior review findings: {feedback}\n\n"
        "Emit a corrected JSON list of assumptions."
    )},
]
corrected = await generate(client, messages=retry_messages, ...)
# Then re-extract context fields with corrected assumptions in scope.
```

## Guardrails required

- **Contradicted-only regeneration.** Skip regen for "unverifiable"
  findings — those are informational, not wrong. Only regenerate when
  the senior engineer has codebase evidence against an assumption.
- **Bounded retry.** One regeneration pass. If the corrected
  assumptions still fail review (rare), accept the original + findings
  rather than ping-ponging.
- **Downstream cascade.** If context changes after assumption fix, the
  architecture and design sections may need re-extraction too. Treat
  this as a deeper problem: does the whole synthesis need to rerun?
  First cut: only re-extract the context section; if downstream reviews
  flag further issues later in the pipeline, they'll catch it.

## Cost

- +1 assumption-regen call (~5-10s) when contradictions exist
- +1 context re-extraction (~30s) — expensive, only pays off if the
  assumption was load-bearing

## Expected lift

Hard to estimate without observing real-world hit rate. On tasks where
the model guesses wrong about the codebase (e.g. assumes
"TeamCollection always has an owner" when the code has an ownerless
branch), the fix prevents the whole plan from being built on a false
premise. On tasks with only plausible uncertainty (rare guesses) the
review fires but passes — no cost.

## Test strategy

Once implemented: craft a synthetic task where the codebase explicitly
contradicts one assumption the model is likely to make. Expected:

- Review fires, flags the contradiction with a file reference
- Regeneration produces a corrected assumption list
- Re-review passes
- Context.assumptions in the output is the corrected list
- Context.review_findings records that the correction happened (audit trail)

## Out of scope

- Architecture / design re-extraction after context correction. Deeper
  question, separate follow-up.
- User-supplied assumption overrides (the `refine` command already on
  the roadmap is the user-facing version of this).
