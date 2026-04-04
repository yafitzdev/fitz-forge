# Confidence Scoring

## Problem

Not all plan sections are equally reliable. A small local LLM might produce an
excellent context analysis but a vague architecture section, or a detailed roadmap
with generic risks. The system needs a way to quantify section quality so it can
flag weak sections for human review (API review pause) and give the user a calibrated
sense of which parts of the plan to trust. A single overall score hides section-level
variation; a purely LLM-based score is unreliable when the same model scores its own
work.

## Solution

A hybrid scorer combining LLM self-assessment (70% weight) with deterministic
heuristics (30% weight). Each plan section is scored independently using
section-specific criteria. The LLM rates on a 1-10 scale mapped to 0.0-1.0.
Heuristics measure length, specificity keywords, and structural markers. When no
LLM is available, the scorer falls back to heuristics only. Two thresholds control
review triggers: a default threshold and a stricter security threshold.

## How It Works

### Scoring Formula

```
hybrid_score = 0.7 * llm_score + 0.3 * heuristic_score
```

When `ollama_client` is `None`, the scorer uses `heuristic_score` alone.

### LLM Self-Assessment

The `_llm_assessment` method constructs a rating prompt with:

- A 1-10 scale with descriptive anchors:
  - 1-2: Missing, incoherent, or fundamentally wrong
  - 3-4: Vague, generic, or ignores existing codebase
  - 5-6: Adequate but could be more concrete or grounded
  - 7-8: Good -- specific, actionable, grounded in codebase
  - 9-10: Excellent -- production-ready, correctly leverages existing code

- **Section-specific criteria** appended to the prompt based on section name.
- **Grounding check** when codebase context is provided: the LLM checks whether the
  section references real files and APIs from the codebase or hallucinates them.
- **Codebase context block** injected as "ground truth" for the grounding check.

The LLM must reply with only a number from 1 to 10. The response is parsed with
`re.search(r"\b(10|[1-9])\b", ...)` and mapped to 0.0-1.0 by dividing by 10.
On parse failure or exception, the default 0.5 is returned.

### Section-Specific Criteria

Each section has its own scoring rubric:

| Section               | Key Criteria                                                        |
|-----------------------|---------------------------------------------------------------------|
| `context`             | Testable requirements, real existing files, genuinely new artifacts, explicit assumptions |
| `architecture`        | Different approaches (not variations), matches codebase patterns, honest scope statement |
| `design`              | Real decision tradeoffs in ADRs, real function signatures in interfaces, grounded data model |
| `architecture_design` | Combined criteria from architecture and design                      |
| `roadmap`             | Concrete deliverables, realistic effort estimates, executable verification commands |
| `roadmap_risk`        | Combined criteria from roadmap and risk                             |
| `risk`                | Specific technical causes, justified impact/likelihood, concrete mitigation actions |

### Heuristic Scoring

Three sub-scores averaged together:

**Length score** (`_length_score`):
- Under 50 chars: 0.2 (too short)
- 50-149 chars: 0.5 (minimal)
- 150-299 chars: 0.8 (good)
- 300+ chars: 1.0 (detailed)

**Specificity score** (`_specificity_score`):
- Counts specificity keywords: `implementation`, `function`, `class`, `method`,
  `module`, `file`, `database`, `api`, `endpoint`, `schema`, `test`, `step`,
  `algorithm`, `protocol`, `interface`.
- Counts vague keywords: `maybe`, `possibly`, `probably`, `should`, `could`, `might`,
  `unclear`, `unknown`, `tbd`, `todo`, `placeholder`, `example`, `etc`.
- No specificity + vague present: 0.3
- No specificity, no vague: 0.5
- More vague than specific: 0.6
- Specific dominates: 1.0

**Structure score** (`_structure_score`):
- Has bullet points or numbered lists: 1.0
- Has multiple paragraphs (newlines): 0.7
- Single block of text: 0.5

### Thresholds

Two thresholds control when sections are flagged for review:

| Threshold             | Value | Purpose                                        |
|-----------------------|-------|------------------------------------------------|
| `default_threshold`   | 0.7   | General quality gate for all sections          |
| `security_threshold`  | 0.9   | Stricter gate for security-sensitive sections  |

Sections scoring below their applicable threshold trigger the API review pause in the
pipeline, where the user can approve or reject the plan before it is rendered to
markdown.

### Codebase-Grounded Assessment

When `codebase_context` is provided to `score_section`, the LLM prompt includes a
grounding check instruction:

> Does the section reference real files, APIs, and patterns from the codebase context?
> Hallucinated references or proposing to build something that already exists should
> lower the score significantly.

This makes the scorer sensitive to the most common failure mode of local LLMs:
hallucinating file names, function signatures, and API endpoints that do not exist.

## Key Design Decisions

1. **Hybrid over pure LLM.** An LLM rating its own work is inherently biased (tends
   toward 7-8 regardless of quality). Heuristics provide an uncorrelated signal that
   catches obvious problems (too short, all vague language, no structure).
2. **70/30 weighting.** The LLM component has higher weight because it can assess
   semantic quality (is the architecture appropriate?) while heuristics only measure
   surface features. But 30% heuristic weight prevents the LLM from giving an 8 to
   a three-word answer.
3. **1-10 scale over 1-5.** Finer granularity gives the LLM more room to express
   uncertainty. A 6 vs 7 distinction is meaningful ("adequate" vs "good") and maps
   cleanly to the 0.0-1.0 output range.
4. **Section-specific criteria over generic.** A roadmap section should be judged on
   deliverable concreteness and verification commands, not the same criteria as a risk
   section. Generic "is this good?" prompts produce undifferentiated scores.
5. **Heuristics-only fallback.** When no LLM client is available (e.g., during testing
   or when the model server is down), the scorer still produces useful scores rather
   than failing.

## Configuration

| Setting               | Default | Description                                 |
|-----------------------|---------|---------------------------------------------|
| `default_threshold`   | `0.7`   | Minimum score before flagging for review   |
| `security_threshold`  | `0.9`   | Stricter threshold for security sections   |

## Files

| File                                            | Role                                      |
|-------------------------------------------------|-------------------------------------------|
| `fitz_forge/planning/confidence/scorer.py`      | `ConfidenceScorer` implementation         |

## Related Features

- **Pipeline Orchestrator** -- Invokes the scorer after coherence check, before
  API review decision.
- **API Review Pause** -- Triggered when any section scores below its threshold.
  The job enters `AWAITING_REVIEW` state until the user confirms or cancels.
- **Plan Renderer** -- Includes confidence scores in the rendered markdown plan
  for user visibility.
- **Coherence Check** -- Runs before scoring so the scorer evaluates the
  coherence-corrected outputs.
