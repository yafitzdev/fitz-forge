# Features

Detailed documentation for every component of fitz-forge's planning pipeline.

## Classic Pipeline Stages (PlanningPipeline)

The original 3-stage pipeline. Numbered in execution order.

| # | Doc | Stage | Description |
|---|-----|-------|-------------|
| 00 | [Agent Context Gathering](pipeline/00_agent-context-gathering.md) | Pre-stage | Retrieve and compress relevant codebase files |
| 01 | [Implementation Check](pipeline/01_implementation-check.md) | Pre-stage | Detect if the task is already built |
| 02 | [Context Stage](pipeline/02_context-stage.md) | Stage 1 | Extract requirements, constraints, assumptions |
| 03 | [Architecture + Design Stage](pipeline/03_architecture-design-stage.md) | Stage 2 | Explore approaches, produce ADRs, components, artifacts |
| 04 | [Roadmap + Risk Stage](pipeline/04_roadmap-risk-stage.md) | Stage 3 | Build phased plan with risk assessment |
| 05 | [Coherence Check](pipeline/05_coherence-check.md) | Post-stage | Cross-stage consistency verification |
| 06 | [Confidence Scoring](pipeline/06_confidence-scoring.md) | Post-stage | Per-section quality assessment |

## Decomposed Pipeline Stages (DecomposedPipeline)

The v0.5+ decision-based pipeline. Shares pre-stages (00, 01) and post-stages (05, 06) with the classic pipeline, but replaces the three planning stages with decision decomposition → resolution → synthesis.

| # | Doc | Stage | Description |
|---|-----|-------|-------------|
| 07 | [Call Graph Extraction](pipeline/07_call-graph-extraction.md) | Pre-stage | Deterministic AST caller→callee chain (no LLM) |
| 08 | [Decision Decomposition](pipeline/08_decision-decomposition.md) | Stage 1 | Break task into atomic decisions with dependencies |
| 09 | [Decision Resolution](pipeline/09_decision-resolution.md) | Stage 2 | Resolve each decision with focused codebase evidence |
| 10 | [Synthesis](pipeline/10_synthesis.md) | Stage 3 | Narrate committed decisions into the final plan |

## Infrastructure

Cross-cutting concerns that support both pipelines.

| Doc | Feature | Description |
|-----|---------|-------------|
| [Per-Field Extraction](infrastructure/per-field-extraction.md) | Extraction | How small models produce reliable structured output |
| [Split Reasoning](infrastructure/split-reasoning.md) | Context management | Reducing peak context for smaller models |
| [Crash Recovery](infrastructure/crash-recovery.md) | Reliability | Checkpoint-based pipeline resumption |
| [LLM Providers](infrastructure/llm-providers.md) | Abstraction | Ollama, LM Studio, llama.cpp backends |
| [Verification Agents](infrastructure/verification-agents.md) | Quality | 5 post-reasoning agents that catch architectural flaws |
| [Grounding Validation](infrastructure/grounding-validation.md) | Quality | AST + LLM validation of generated artifacts |

## Reference

| Doc | Description |
|-----|-------------|
| [Architecture](../ARCHITECTURE.md) | System overview, layer diagram, data flow |
| [Configuration](../CONFIG.md) | Every config field explained |
| [Troubleshooting](../TROUBLESHOOTING.md) | Common issues and solutions |
