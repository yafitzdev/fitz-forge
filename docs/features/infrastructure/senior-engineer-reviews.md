# Senior Engineer Reviews

## Problem

A small local model writing an architectural plan is a junior engineer left
unsupervised. It asks the wrong questions, makes plausible-sounding wrong picks,
under-specifies interfaces, builds on assumptions the codebase contradicts, and
forgets to deliver files it already committed to. Best-of-N sampling and
self-critique catch consistency issues inside a single output, but not quality
issues — a plan can be internally consistent *for the wrong recommendation* and
every gate downstream happily propagates the defect.

What's missing is what a senior engineer would provide at each stage of a real
design review: a second opinion on *this specific stage's output*, scoped tightly
enough that a small model can give it sharply, wired to regenerate the affected
stage when the critique is substantive.

## Solution

A composable review framework sitting on top of the pipeline. Each review is a
narrow critique pass that runs at a specific stage output, returns a uniform
`ReviewResult`, and — when it finds issues — hands the feedback back to the
stage so it can regenerate under the critique pressure.

Every review follows the same three-beat pattern:

1. **Detect** — one LLM call (or a deterministic check) focused on one question:
   "what would a senior engineer say about this particular piece of the plan?"
2. **Regenerate** — when issues found, regenerate the stage-specific output
   with the feedback merged into the prompt. The *mechanism* differs by stage
   (reasoning regeneration vs field re-extraction vs per-file artifact build),
   but the *pattern* is uniform.
3. **Re-review** — run the same review on the regenerated output. Keep
   whichever pass has fewer issues. Remaining issues attach to
   `review_findings` on the stage output so the plan surfaces them to the
   downstream coder.

Fail-safe on every error: an exception anywhere in the loop keeps the original.
A broken LLM response cannot block the pipeline.

## How It Works

### Unified Shape

Every review returns a `ReviewResult` from `fitz_forge/planning/reviews/base.py`:

```python
@dataclass
class ReviewResult:
    scope: str                      # "architecture", "design", ...
    passed: bool
    issues: list[ReviewIssue]
    raw_response: str = ""
```

And every issue follows the same shape — what a senior engineer would write on
a design-review comment:

```python
@dataclass(frozen=True)
class ReviewIssue:
    scope: str          # which review produced it
    target: str         # decision id, component name, filename, "data_model"
    intent: str         # what the senior engineer expected
    actual: str         # what is currently there
    suggestion: str     # concrete change to close the gap
```

This uniformity is load-bearing. Because the shape is identical across all
reviews, the regeneration path can call `format_issues_feedback(issues)` —
one helper that renders any review's findings into a prompt-ready block —
without knowing which review produced them.

### The Reviews

Six reviews exist today, each scoped to one stage output:

| Review | Fires after | Catches |
|--------|-------------|---------|
| `review_decomposition` | Decision decomposition | Disguised pattern pre-commits ("how should we implement X?" — but X was never the right question). Missing critical questions the task's call chain requires. |
| `review_assumptions` | Context extraction | Assumptions the codebase demonstrably contradicts, and high-impact assumptions the model has no evidence for. |
| `review_architecture` | Architecture extraction | Wrong-pick outliers: plausible-sounding-but-taxonomy-worst patterns that slip past self-critique because the plan is internally consistent *for the wrong pick*. |
| `review_design` | Design extraction (pre-artifact) | Under-specified interfaces, data models that don't enumerate rubric-mandated field names, leaky typed contracts, missing components in the call chain. |
| `review_artifacts` (semantic) | Artifact generation | Intent-vs-code contradictions — the code *runs* but does something different from what the plan says it does. |
| `review_artifact_coverage` | Artifact generation | Files listed in `context.needed_artifacts` that didn't ship as artifacts. Set-level invariant check — deterministic, no LLM call. |

Three more reviews (`risk`, `roadmap`, `coherence-level-senior`) are plausible
future additions; the framework is built to grow that way without changing the
existing call sites.

### Regeneration Mechanisms

The *mechanism* each review uses to regenerate its stage output matches what
that stage actually consumes:

- **Decomposition review → full decomposition rerun.** The decomposition stage
  generates candidate decision sets; regeneration re-runs that generator with
  the critique appended.
- **Architecture review → reasoning regeneration.** The architecture is
  extracted from the synthesis reasoning text. Re-extracting from the same
  text can never flip the pick — it still argues for the wrong approach. So
  architecture regen rewrites the reasoning itself with the critique in the
  prompt, runs a scope/coherence sanity gate on the new reasoning (rejecting
  truncated or empty rewrites below 70% of the original's score), then
  re-extracts.
- **Design review → field-group re-extraction + cascade to artifacts.** Design
  regen re-extracts only the field groups the issues point to (`components`,
  `data_model`, `adrs`, `integrations`) with feedback appended. Crucially,
  **the review runs BEFORE per-file artifact generation**, and the issues
  cascade into the reasoning text that the artifact generator reads — so
  field-name precision from the review actually reaches the generated code.
  Without this cascade the review only improves the plan markdown, not the
  artifact content.
- **Assumption review → surface only (regen in spec).** Currently attaches
  contradicted assumptions as review findings for the downstream coder.
  Context-regeneration is specified but not yet implemented (see
  `docs/senior-engineer-todo/02-assumption-review-regeneration.md`).
- **Semantic review → per-file regeneration with feedback.** When the semantic
  gate finds an intent-vs-code contradiction, the specific artifact is
  regenerated with the critique pointing at the mismatch.
- **Artifact coverage → targeted rebuild.** When needed files are missing
  from the artifact set, their `(filename, purpose)` pairs are routed
  directly into `generate_artifact_set`. Files that still fail to build
  surface as `review_findings`.

### Fail-Safe Everywhere

Every review pass wraps its LLM call in `try`/`except`. An exception returns
the original output unchanged. Every regeneration path does the same:
exception → keep original. Validation failure on the regenerated output →
keep original. Retry review with more issues than the original → keep
original.

The invariant is: *no review can make the plan worse than the one without the
review*. This is what makes it safe to stack reviews — each one is strictly
additive, either improving the stage output or returning it unchanged.

### Deterministic Reviews

Not every "what would a senior engineer say" requires an LLM. The artifact-
coverage review is a set-difference between filenames — exact, fast,
deterministic. It uses the same `ReviewResult` / `ReviewIssue` shape so the
caller and regeneration path look identical, but the detection phase is a
`set.difference()`.

This matters for two reasons. First, it's the right level for set-level
invariants (Rule 11: "watch for set-level bugs" — these can never be caught by
per-item validation). Second, it keeps the framework honest: when an LLM
check isn't needed, don't pay for one.

## Key Design Decisions

1. **Shape before substance.** Every review returns the same
   `ReviewResult`/`ReviewIssue`. Regeneration paths differ because stages
   differ, but the critique surface is uniform. This is what makes the
   framework composable — new reviews add sibling modules in
   `planning/reviews/` and wire into one more call site, no refactor.

2. **Right mechanism per stage.** Architecture gets reasoning-regen because
   the architecture is argued for in the reasoning text. Design gets field
   re-extraction because the design is a structured schema. Coverage gets
   targeted artifact rebuild because artifacts are per-file. Forcing a single
   regeneration mechanism across all reviews would either leave architectures
   unflippable (re-extract-only) or make simple schemas expensive
   (reasoning-regen for everything).

3. **Detect before fix.** Every review starts as detect-only (attach findings
   to the stage output for the downstream coder). Regeneration is a
   follow-up once the detection pattern is battle-tested. This staged
   delivery keeps the framework safe to extend.

4. **Fail open, never closed.** A broken LLM response, a prompt parse
   failure, a validation exception — none of these block the pipeline.
   Reviews can only make the plan better or leave it unchanged.

5. **Reviews replace prompt bandaids.** When a failure class emerges,
   the instinctive question is not "what prompt fix catches this?" but
   "what would a senior engineer reading this stage's output say, and
   which existing review is the right place for that?" Prompt fixes
   calcify into a maze of pattern-matchers (Rule 12); a review is one
   LLM call that catches the whole class.

## Adding a New Review

1. Create `fitz_forge/planning/reviews/<scope>.py`. Write a single
   `async def review_<scope>(...)` returning `ReviewResult`. System prompt
   and user prompt are tightly scoped to one stage output.
2. Export from `fitz_forge/planning/reviews/__init__.py`.
3. Wire into the relevant stage: detect-only first (attach to
   `review_findings`). Write wiring tests. Ship.
4. Add regeneration in a follow-up: pick the mechanism matching what the
   stage consumes, add guardrails (bounded retry, scope sanity gate if the
   stage has one, fail-safe on every error).

Never tighten review output parsing beyond fail-open — the parser must
accept legacy shapes from drifted models (the semantic review's parser
still accepts the old `matches_intent`/`discrepancies`/`fix` shape
alongside the unified one for this reason).

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/reviews/base.py` | `ReviewResult`, `ReviewIssue`, `format_issues_feedback` |
| `fitz_forge/planning/reviews/decomposition.py` | Decomposition critique |
| `fitz_forge/planning/reviews/architecture.py` | Architecture pick critique |
| `fitz_forge/planning/reviews/assumptions.py` | Adversarial assumption check |
| `fitz_forge/planning/reviews/design.py` | Design specificity critique |
| `fitz_forge/planning/reviews/semantic.py` | Artifact intent-vs-code check |
| `fitz_forge/planning/reviews/artifact_coverage.py` | Set-level coverage check (deterministic) |
| `fitz_forge/planning/pipeline/stages/synthesis.py` | Wiring for 5 of the 6 reviews |
| `fitz_forge/planning/pipeline/stages/decision_decomposition.py` | Wiring for decomposition review |
| `fitz_forge/planning/artifact/generator.py` | Wiring for semantic review |

## Cost

Per synthesis run, all reviews together add roughly:

- 5 LLM review calls (~5-10s each) — one per LLM-based review
- 0 LLM calls for the coverage review (deterministic)
- Regeneration calls only when issues are found — clean plans pay nothing
  beyond detection

Typical overhead: ~30-60s on synthesis runs that have issues worth fixing,
~25-50s on clean runs (detection only). Compared to the baseline synthesis
of ~8-15 minutes, that's a 5-10% tax for a significant correctness lift.

## Related Features

- [Synthesis](../pipeline/06_synthesis.md) — the stage where most reviews
  are wired
- [Artifact Generation](../pipeline/07_artifact-generation.md) — wiring for
  semantic and coverage reviews
- [Per-Field Extraction](per-field-extraction.md) — the field-group
  extraction primitive that design regeneration reuses
