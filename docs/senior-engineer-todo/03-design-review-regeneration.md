# Design review — upgrade from surface-findings to design regeneration

**Status:** open
**Source:** 2026-04-19 MVP landing
**Blocks:** K1/RR1 ceiling on tasks whose A1 requires specific field names

## What works today

The design review (`reviews/design.py`) fires after the design section
is assembled and before artifact generation begins. It critiques:

1. Under-specified interfaces ("record ranking signals" without names)
2. Data models that don't enumerate the specific fields the rubric
   expects
3. Leaky contracts (dict where typed record belongs)
4. Missing components / skipped call-chain layers
5. ADRs that paper over rubric divergence

Issues attach to `design.review_findings` as plan diagnostics. The
downstream coder sees the specific gaps before acting.

## What doesn't work

Artifact generation still reads the original (under-specified) design.
Even though the review flags "Ranker must record `base_score,
strategy_weight, entity_bonus, keyword_boost, composite_score`
separately," the component's `interfaces` list still says "record
ranking signals" and the artifact generator follows that lead.

This is why the MVP can detect the K1 ceiling but not flip K2 plans
to K1 — the design fix has to reach the artifact generator, and right
now findings are an advisory side-channel.

## The fix

When the design review finds issues, **regenerate the affected design
fields** (components, data_model, artifacts) with the feedback merged
into the extraction prompt. Then artifact generation reads the
updated design, not the original.

Two sub-cases:

**Case A: interface under-specification.** The component's
`interfaces` list is too vague. Re-run the `components` extraction
with the review feedback appended to the reasoning. Keep other fields
(purpose, responsibilities) stable — only the interface list should
shift.

**Case B: data-model field-name gaps.** The data model needs specific
field names. Re-run the `data_model` extraction with feedback listing
the expected field names. The rubric already has them — the fix is
making the model actually copy them into the design.

**Case C: missing component / layer skip.** The design lacks a
component for a layer in the call chain. Append a synthesized
component via the feedback ("add a `Service` component between Route
and Engine with interface X"). Re-run components extraction.

## Guardrails required

- **Bounded retry.** One regeneration pass per issue class.
- **Field-level scoping.** Only regenerate the fields the issues point
  to; don't rewrite the whole design section (that risks breaking
  good fields).
- **Validation.** `DesignOutput(**merged)` must still pass after
  regen. Roll back if it doesn't.
- **Cascade.** If design changes, artifact generation reads the new
  design. That's already the case — no extra wiring needed.

## Cost

- +1 review call (~5-10s) — already paid by MVP
- +1-3 field-extraction calls (~5-15s each) only when issues exist
- Minimal overhead on clean designs

## Expected lift

Probably the highest remaining lever for ranking's Tier-2:

- K2 → K1 on ranker.py: if the design lists the five signal fields,
  the code will record them (~25 points on the file)
- RR2 → RR1 on reranker.py: if the design says "preserve
  pre_rerank_score before overwriting," the code will do it (~50
  points on the file)
- A2 → A1 on the architecture: derivative of the above, since A1 is
  scored by the composite of the file tiers

Estimated ranking Tier-2 lift: 74.5 → 85+ if all three per-file
fixes land together.

## Test strategy

Once implemented: replay ranking_explanation from
snapshot_after_decision_resolution.json with the rubric already
loaded. Expected:

- Design review fires with issues on ranker.py / reranker.py
- Regeneration updates the component interfaces with literal field
  names
- Subsequent artifact generation emits code that records them
- Tier-2 scoring picks up K1 / RR1 on 3+ of 5 plans

## Out of scope

- Cross-stage feedback loops (design issues triggering re-run of
  decision resolution). That's a deeper refactor — the review passes
  should stay stage-local.
- Rubric-driven design templates (pre-baking the five signal fields
  into the data model before review). Different mechanism; the review
  stays observation-based.
