# 09 — Decision Resolution

## Problem

Once a task is decomposed into atomic decisions, each decision needs to be resolved with *focused* codebase evidence. A monolithic call can't fit all the source code. But each decision only needs 1-3 files — the specific files where that decision plays out. The challenge is giving each decision exactly the right context while propagating constraints from earlier decisions.

## Solution

Process decisions in topological order (Kahn's algorithm). Each decision gets one LLM call with focused context: the decision question + its call graph segment + full source of 1-3 relevant files + constraints committed by upstream dependencies. The model commits a decision and declares constraints that downstream decisions must respect.

## How It Works

### Topological Sort

Decisions are sorted using Kahn's algorithm based on `depends_on` edges. Independent decisions process first; dependent decisions wait for their upstream constraints. If the graph has cycles, back-edges are broken and remaining decisions process in original order with a warning.

### Per-Decision Context Assembly

For each decision, the stage builds a focused prompt:

1. **Call graph segment** — `CallGraph.segment_for_files(relevant_files)` extracts only the portion of the call graph involving this decision's files
2. **Source code** — Full compressed source of up to 3 relevant files. If a file isn't in the agent's pool (e.g., discovered by BFS beyond the selected set), it's read from disk as a fallback
3. **Structural fallback** — If source isn't available at all, the file's structural index entry (classes, methods, imports) is used instead
4. **Upstream constraints** — Constraints committed by already-resolved dependencies, injected as "CONSTRAINTS FROM PREVIOUS DECISIONS (you MUST respect these)"

### Resolution Output

Each LLM call produces a `DecisionResolution`:

```json
{
  "decision_id": "d1",
  "answer": "Add WebSocket handling to the existing ChatRouter...",
  "reasoning": "The existing router already handles auth...",
  "constraints": [
    "WebSocket connections must go through ChatRouter.ws_chat()",
    "Auth verification uses existing AuthMiddleware.verify_token()"
  ],
  "affected_files": ["api/routes/chat.py"],
  "confidence": "high"
}
```

The `constraints` array is the key propagation mechanism — downstream decisions that `depend_on` this one receive these constraints in their prompts.

### Contradiction Check

After all decisions are resolved, an optional LLM call scans the full set of resolutions for direct contradictions (e.g., decision A says "providers already have `chat_stream()`" but decision B says "providers do not have `chat_stream()` yet"). Contradictions are flagged in the output for the synthesis stage to reconcile.

### Token Budget

Each resolution call uses ~4-8K input tokens — small enough for any context window. The focused context means the model sees the actual source code of the files it's deciding about, not a vague summary.

## Key Design Decisions

1. **One call per decision.** Not batched. Each decision gets its own LLM call with tailored context. This is more calls but each is focused and reliable.

2. **Topological ordering enforces coherence.** Decision d2 (auth integration) can't be resolved until d1 (router choice) commits. This prevents contradictory decisions — earlier choices constrain later ones.

3. **Disk fallback for uncovered files.** The call graph BFS may discover files beyond the agent's selected set. Rather than skip them, the resolution stage reads them from disk and applies the same compression. This ensures decisions about deep implementation details have real source code.

4. **Constraints are text, not schema.** Constraints are free-form strings, not structured data. This lets the model express nuanced constraints ("use existing `verify_token()` but add a `ws_upgrade` parameter") that don't fit a rigid schema.

5. **Contradiction detection is post-hoc.** Rather than trying to prevent contradictions during resolution (which would require global context), the stage detects them after all resolutions complete. This keeps individual calls focused while still catching inconsistencies.

## Configuration

No user-facing configuration. The resolution stage runs automatically as part of the `DecomposedPipeline`.

| Internal | Value | Description |
|----------|-------|-------------|
| Progress range | 0.20–0.75 | Largest progress range — this is the most LLM-intensive stage |
| Max source files per decision | 3 | Compressed source of the most relevant files |
| Token budget per call | ~4-8K | Focused context for each decision |

## Files

| File | Description |
|------|-------------|
| `fitz_forge/planning/pipeline/stages/decision_resolution.py` | `DecisionResolutionStage`, `_topological_sort()` |
| `fitz_forge/planning/schemas/decisions.py` | `DecisionResolution`, `DecisionResolutionOutput` |
| `fitz_forge/planning/prompts/decision_resolution.txt` | Prompt template |
| `fitz_forge/planning/pipeline/call_graph.py` | `CallGraph.segment_for_files()` — subgraph extraction |

## Related Features

- [Decision Decomposition](08_decision-decomposition.md) — produces the decisions resolved here
- [Synthesis](10_synthesis.md) — narrates the committed decisions into the final plan
- [Call Graph Extraction](07_call-graph-extraction.md) — provides per-decision subgraph segments
