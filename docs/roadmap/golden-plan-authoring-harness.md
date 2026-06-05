# Golden Plan Authoring Harness

## Problem

The project evaluates planning artifacts, not finished patches. That means a benchmark needs an answer key that describes what a good plan should understand before any local model output is scored.

Today, those golden planning artifacts are created manually with help from a large model such as Claude Sonnet. The process works, but it is not yet a first-class tool.

## Proposed Tool

Add a benchmark authoring command that uses a frontier model to create the golden plan artifacts for a new challenge:

```bash
.venv/Scripts/python -m benchmarks.author_challenge \
  --source-dir ../target-repo \
  --query-file benchmarks/challenges/new_task/user_prompt.txt \
  --out benchmarks/challenges/new_task
```

The command should guide the model through repository inspection and produce:

- `golden_plan.md` - the ideal architecture and implementation plan.
- `taxonomy.json` - architecture tiers, per-file tiers, quality labels, and scores.
- `ideal_context.json` - files and structural context required to understand the task.
- `rubric.md` - human-readable quality criteria that can be injected into benchmark runs.
- `AUTHORING_REPORT.md` - validation notes and unresolved questions for human review.

## Sonnet Workflow

The authoring harness should run the large model in constrained passes instead of asking for the final taxonomy in one shot:

1. Inspect the task and target repository.
2. Identify required, recommended, and optional files.
3. Draft the ideal implementation plan.
4. Enumerate plausible degraded approaches and failure modes.
5. Convert the plan into architecture taxonomy tiers.
6. Convert critical files into per-file taxonomy tiers.
7. Validate the taxonomy schema and check that referenced files exist.
8. Ask for a final self-review focused on ambiguity, missing tiers, and overfitted criteria.

The output should still require human review. The large model drafts the answer key; the benchmark owner accepts, edits, or rejects it.

## Validation Rules

The tool should fail or warn when:

- `taxonomy.json` does not match the scorer schema.
- A required file does not exist in the target repository.
- The architecture taxonomy has no ideal tier or no failing tier.
- Required files lack per-file taxonomy entries.
- A taxonomy tier depends on vague language such as "good enough" without observable criteria.
- The golden plan uses APIs that do not exist in the target codebase.
- The rubric and taxonomy disagree about required behavior.

## Why This Matters

This makes the benchmark story credible. Local models are not judged by open-ended preference; they are judged against a frontier-model-assisted, human-reviewed answer key. The research question becomes measurable: does the harness move a weak local model closer to the golden plan?
