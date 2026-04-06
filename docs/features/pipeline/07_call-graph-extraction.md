# 07 — Call Graph Extraction

## Problem

When a local LLM decomposes a task into decisions, it needs to know which files are connected — which file calls which, which class depends on which. Without this, the model proposes changes to isolated files with no awareness of how they integrate. A 30B model doesn't have the context budget to read the entire codebase, so it needs a pre-computed map of the architecture.

## Solution

Deterministic call graph extraction from Python AST + import graph. Pure Python — no LLM calls. The call graph identifies entry points matching the task description, follows import edges via BFS, and produces an ordered caller→callee chain that tells the decomposition stage exactly which files are involved and how they connect.

## How It Works

### Entry Point Discovery

The extractor searches the structural index for symbols matching keywords from the task description. If the task says "Add WebSocket support to the chat API", it finds `ChatRouter`, `chat_endpoint()`, `ChatEngine` as entry points.

### BFS Import Traversal

From each entry point, the extractor follows import edges in the forward map (built by `build_import_graph` from AST). BFS runs to configurable `max_depth` (default 3). This captures the full call chain: route handler → engine → provider → client.

### Graph Construction

The result is a `CallGraph` dataclass:
- **nodes**: `CallGraphNode` per file — file path, relevant symbols, one-line summary, depth from entry point, class detail (e.g., `FitzKragEngine [answer -> Answer, ...]`)
- **edges**: `(source_file, target_file)` pairs representing import relationships
- **entry_points**: files matching task keywords (depth 0)
- **max_depth**: how deep the BFS went

### Prompt Formatting

`CallGraph.format_for_prompt()` produces a compact text representation:

```
CALL GRAPH (caller -> callee):
  api/routes/chat.py -> engines/chat_engine.py  # ChatRouter uses ChatEngine
  engines/chat_engine.py -> llm/client.py       # ChatEngine uses LLMClient

FILES (depth-ordered):
  [0] api/routes/chat.py — HTTP route handlers
  [1] engines/chat_engine.py — Chat engine orchestration
  [2] llm/client.py — LLM client interface
```

Nodes are sorted by depth — entry points first, then immediate callees, then deeper dependencies. Capped at `max_nodes=60` to stay within token budget.

### Subgraph Extraction

`CallGraph.segment_for_files(file_paths)` extracts a focused subgraph for a specific decision. The decision resolution stage uses this to give each decision only the relevant portion of the call chain.

## Key Design Decisions

1. **No LLM calls.** The call graph is deterministic — same codebase, same result. This keeps it fast (< 1s) and reproducible.

2. **BFS from seed files, not all-pairs.** Only files reachable from task-relevant entry points are included. This keeps the graph focused rather than showing the entire import tree.

3. **Import graph covers ALL indexed files.** BFS can traverse edges beyond the selected 30 files (e.g., `engine.py → synthesizer.py` when `synthesizer.py` wasn't selected). Keyword matching stays on the selected-only structural index to keep entry points focused.

4. **Depth-ordered output.** Sorting by depth ensures the LLM sees the most relevant files (entry points) first, with deeper implementation details further down. If the prompt truncates, the least important files are lost first.

5. **Class detail annotations.** Edges are annotated with class/method symbols when both sides have detail and the annotation fits (< 400 chars). This tells the model *what* is called, not just *which file*.

## Configuration

No user-facing configuration. The call graph is always extracted when using the decomposed pipeline.

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_depth` | 3 | BFS traversal depth from entry points |
| `max_nodes` | 60 | Maximum files in prompt output |

## Files

| File | Description |
|------|-------------|
| `fitz_forge/planning/pipeline/call_graph.py` | `CallGraph`, `CallGraphNode`, `extract_call_graph()` |
| `fitz_forge/planning/agent/indexer.py` | `build_import_graph()` — AST-based import edge extraction |

## Related Features

- [Agent Context Gathering](00_agent-context-gathering.md) — provides the structural index and file list
- [Decision Decomposition](08_decision-decomposition.md) — consumes the call graph to identify which decisions need which files
- [Decision Resolution](09_decision-resolution.md) — uses `segment_for_files()` for focused per-decision context
