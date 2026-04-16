# Features

Detailed documentation for every stage of the fitz-forge planning pipeline.

## Pipeline Stages

In execution order.

| # | Doc | Role |
|---|-----|------|
| 1 | [Agent Context Gathering](pipeline/01_agent-context-gathering.md) | Codebase retrieval + compression |
| 2 | [Implementation Check](pipeline/02_implementation-check.md) | Detect if the task is already built |
| 3 | [Call Graph Extraction](pipeline/03_call-graph-extraction.md) | Deterministic AST dependency graph (no LLM) |
| 4 | [Decision Decomposition](pipeline/04_decision-decomposition.md) | Break task into atomic decisions |
| 5 | [Decision Resolution](pipeline/05_decision-resolution.md) | Resolve each decision with focused evidence |
| 6 | [Synthesis](pipeline/06_synthesis.md) | Narrate committed decisions into the plan |
| 7 | [Artifact Generation](pipeline/07_artifact-generation.md) | Generate code + set-level closure checks |
| 8 | [Grounding Validation](pipeline/08_grounding-validation.md) | AST + LLM repair of generated artifacts |
| 9 | [Coherence Check](pipeline/09_coherence-check.md) | Cross-stage consistency verification |

## Infrastructure

Cross-cutting concerns used by the pipeline.

| Doc | Description |
|-----|-------------|
| [Per-Field Extraction](infrastructure/per-field-extraction.md) | How small models produce reliable structured output |
| [Crash Recovery](infrastructure/crash-recovery.md) | Checkpoint-based pipeline resumption |
| [LLM Providers](infrastructure/llm-providers.md) | Ollama, LM Studio, llama.cpp backends |

## Reference

| Doc | Description |
|-----|-------------|
| [Architecture](../ARCHITECTURE.md) | System overview, layer diagram, data flow |
| [Configuration](../CONFIG.md) | Every config field explained |
| [Troubleshooting](../TROUBLESHOOTING.md) | Common issues and solutions |
