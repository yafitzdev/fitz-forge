# Architecture review — upgrade regeneration from re-extraction to reasoning-regeneration

**Status:** landed 2026-04-19
**Source:** 2026-04-19 replay validation on streaming run_042 plan_04 (A4 outlier)
**Landed in:** `fitz_forge/planning/pipeline/stages/synthesis.py`
(`_senior_arch_review_pass` now regenerates the synthesis reasoning
with the critique merged into the prompt, runs a 70%-score sanity
gate on the new reasoning, re-extracts, re-reviews, and keeps
whichever pass has fewer issues). Tests in
`tests/unit/test_synthesis_senior_arch_review_wiring.py` (8).

## Original spec (below) kept for reference

## What works today

The architecture review (commit `feat(reviews): add architecture critique`) correctly **detects** wrong-recommendation outliers. On the plan_04 replay it flagged exactly the issues that Sonnet's Tier-2 grader flagged independently:

- `architecture.approaches` — "Consider an approach that leverages existing provider protocols to avoid massive [changes]. The recommendation treats 'Dual-Path' as a way to avoid modifying core schemas."
- `architecture.reasoning` — "Address how to handle metadata (like token usage or citations) without breaking..."

## What doesn't work

The MVP's regeneration path appends the review feedback to the synthesis reasoning text and re-runs **`_ARCH_FIELD_GROUPS` per-field extraction** from the augmented text. On plan_04 the extraction produced an architecture with *more* review issues (2 → 3) and the fail-safe correctly kept the original.

Root cause: the synthesis reasoning TEXT itself recommends Dual-Path throughout. Bolting a critique block onto the end of that text and re-extracting doesn't rewrite the argument — the extraction still pulls "Dual-Path" because that's what the reasoning argues for.

## The fix

Replace re-extraction with **reasoning-regeneration**: when the architecture review finds issues, call `generate()` again with a prompt that feeds the synthesis prompt + review feedback and asks the model to produce a fresh reasoning text that addresses the critique. Then re-extract architecture fields from the fresh reasoning.

Shape:

```python
# inside _senior_arch_review_pass:
if not review.passed:
    feedback_block = format_issues_feedback(review.issues)
    retry_messages = self.build_prompt(job_description, prior_outputs)
    retry_messages[-1]["content"] += (
        "\n\n## Senior architecture review (rewrite the architecture section to address these):\n\n"
        + feedback_block
    )
    new_reasoning = await generate(
        client,
        messages=retry_messages,
        temperature=0.3,  # allow some creativity — the re-pick may need to be different
        max_tokens=16384,
        label="synthesis_reasoning_after_arch_review",
    )
    # Re-extract architecture from the fresh reasoning
    arch_merged = {}
    for group in _ARCH_FIELD_GROUPS:
        partial = await self._extract_field_group(
            client, new_reasoning, group["fields"], group["schema"],
            f"{group['label']}_after_review",
            extra_context=extract_context,
        )
        arch_merged.update(partial)
```

## Guardrails required

- **Bounded retry**: max 1 reasoning regeneration. If the review still fails, fall back to original.
- **Scoring**: reuse `_score_reasoning` to sanity-check the new reasoning. Reject regeneration if the new reasoning is significantly worse on scope/coherence.
- **Rollback**: keep the original reasoning + architecture as the fallback. Only swap when the new pass has fewer review issues *and* passes scope gate.
- **Log both passes**: original + regenerated should be captured in traces for diagnostic.

## Cost

- +1 reasoning call (~30s) when review finds issues
- +1 review call (~5s) on the regenerated output
- Only fires on actual issues — clean plans pay 0

## Expected lift

On streaming's historical A4 outliers: ~20 Tier-2 points per outlier plan. With 1-2 outliers in a typical 5-plan run, that's ~4-8 average Tier-2 points.

On hoppscotch/ranking: marginal — those tasks were already well-served by decomposition review + rubric. Architecture review is highest-impact on tasks where the reasoning model occasionally picks a plausible-sounding-but-wrong pattern.

## Test strategy

Replay plan_04 (run_042) with the upgrade. Expected:

- Review fires → reasoning regenerates with critique in prompt
- New reasoning argues for a protocol-based approach (not Dual-Path)
- New architecture.recommended is different from the original
- Re-review passes (or has strictly fewer issues than the original)

## Out of scope for this TODO

- Regeneration for the other fields (context, design, roadmap, risk) if their review ever finds issues. Different mechanism, different prompts.
- Scoring variance when retry lands on a different-but-also-bad pick. Current fail-safe catches this via "fewer issues" gate.
