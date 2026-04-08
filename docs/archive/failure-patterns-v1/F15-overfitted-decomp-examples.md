# F15: Overfitted Decomposition Examples

## Problem
`planning/prompts/decision_decomposition.txt` lines 29-31 contain codebase-specific examples:

```
## Good decisions look like:
- "What existing method in synthesizer.py should be extended/wrapped for streaming?"
- "What is the return type contract for the new streaming interface on engine.py?"
- "How does the governance check in engine.py interact with the proposed streaming flow?"
```

These reference specific files (`synthesizer.py`, `engine.py`), specific concepts (`governance check`), and assume the task is always about streaming.

## Impact
- On fitz-sage: probably helps (model sees domain-relevant examples)
- On other codebases: misleads the model with irrelevant file names and concepts
- Violates the principle that the pipeline should be codebase-agnostic

## Affected Stage
`decision_decomposition.txt` — the decomposition prompt template

## Fix
Replace with generic examples that demonstrate the PATTERN (specific, file-referencing questions) without referencing any particular codebase. Use placeholder-style names that make the structure clear.

Decision: do NOT auto-populate examples from codebase scan. That adds complexity and reduces reproducibility. Simple generic examples are sufficient — the model uses the actual codebase context (structural index, file manifest) to make decisions, not the example file names.

## Test Data
- Harness: `benchmarks/test_f1_dedup.py` (reused — decomposition duplicate rate)
- Baseline (overfitted examples): **22% duplicate rate** (11/50), avg 13.7 decisions
- After fix (generic examples): **6% duplicate rate** (3/50), avg 13.6 decisions
- **IMPROVED** — the codebase-specific streaming examples were priming duplicate questions

## Risk
None — verified improvement, no regression on decision count.

## Status: FIXED (22% dupes → 6% dupes)
