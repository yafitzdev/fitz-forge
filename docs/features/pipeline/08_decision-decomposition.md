# 08 — Decision Decomposition

## Problem

A monolithic planning prompt ("design the architecture for X") overwhelms small local models. They can't hold the full codebase context, reason about architecture, produce structured output, and stay coherent — all in one call. The result is generic advice that ignores the actual codebase.

## Solution

Break the task into atomic decisions *before* any heavy reasoning. One cheap LLM call (~2-4K input tokens) takes the task description + call graph + one-line file manifest and produces an ordered list of specific questions that need answering. Each decision can then be resolved independently with focused context.

## How It Works

### Input

The decomposition stage receives:
- **Task description** — the user's natural language request
- **Call graph** — deterministic AST-extracted caller→callee chain (see [Call Graph Extraction](07_call-graph-extraction.md))
- **File manifest** — one-liner per file (path + docstring), not full source

This is deliberately lightweight — the model identifies *what* to decide, not *how* to decide it.

### LLM Call

A single call using the `decision_decomposition.txt` prompt template. The model produces a JSON array of `AtomicDecision` objects:

```json
[
  {
    "id": "d1",
    "question": "Should WebSocket handling be added to the existing ChatRouter or a new dedicated router?",
    "category": "architecture",
    "relevant_files": ["api/routes/chat.py", "api/routes/__init__.py"],
    "depends_on": []
  },
  {
    "id": "d2",
    "question": "How should the WebSocket connection lifecycle integrate with the existing AuthMiddleware?",
    "category": "integration",
    "relevant_files": ["api/middleware/auth.py", "api/routes/chat.py"],
    "depends_on": ["d1"]
  }
]
```

### Coverage Check

After decomposition, the stage checks whether the decisions cover the interior layers of the call graph — not just the entry points and leaf nodes. If middle layers (e.g., the engine that sits between the route and the client) are uncovered, a coverage hint is appended and the model is re-called with guidance to add decisions for those layers.

### Deduplication

The stage removes near-duplicate decisions using `SequenceMatcher` on the question text. If two decisions ask essentially the same question with different wording, the one with fewer dependencies is kept.

### Validation

Output is validated through `DecisionDecompositionOutput` (Pydantic schema in `schemas/decisions.py`). Each decision must have: `id`, `question`, `category`, `relevant_files`, and `depends_on`. Invalid entries are dropped with warnings.

## Key Design Decisions

1. **Cheap call, not smart call.** The decomposition doesn't need the full codebase — it just needs to know what files exist and how they connect. ~2-4K input tokens keeps this fast and reliable even on small models.

2. **Dependencies are explicit.** Each decision declares `depends_on`, creating a DAG that the resolution stage processes in topological order. Decision d2 can't be resolved until d1's constraints are committed.

3. **Coverage verification prevents shallow decomposition.** Without the coverage check, models often decompose into surface-level decisions (entry point + leaf) and miss the orchestration layer in the middle. The hint forces them to address interior layers.

4. **File manifest, not source code.** The decomposition stage sees file paths and one-line docstrings — enough to identify relevant files without burning context on source code. Full source is reserved for the resolution stage.

5. **Implementation check injection.** If the pre-stage detected that the task is already implemented, that signal is injected into the prompt. The model then decomposes around verification and extension rather than building from scratch.

## Configuration

No user-facing configuration. The decomposition runs automatically as the first stage of the `DecomposedPipeline`.

| Internal | Value | Description |
|----------|-------|-------------|
| Progress range | 0.10–0.20 | Where this stage sits in the overall pipeline progress |
| Token budget | ~2-4K input | Deliberately lightweight |

## Files

| File | Description |
|------|-------------|
| `fitz_forge/planning/pipeline/stages/decision_decomposition.py` | `DecisionDecompositionStage` |
| `fitz_forge/planning/schemas/decisions.py` | `AtomicDecision`, `DecisionDecompositionOutput` |
| `fitz_forge/planning/prompts/decision_decomposition.txt` | Prompt template |

## Related Features

- [Call Graph Extraction](07_call-graph-extraction.md) — provides the call graph consumed here
- [Decision Resolution](09_decision-resolution.md) — resolves each decision in topological order
- [Implementation Check](01_implementation-check.md) — signals whether the task is already built
