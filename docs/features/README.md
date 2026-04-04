# Features

Detailed documentation for every component of fitz-forge's planning pipeline.

## Pipeline Stages

Each stage in the planning pipeline has its own doc explaining what it does, why it's designed that way, and how it works internally. Numbered in execution order.

| # | Doc | Stage | Description |
|---|-----|-------|-------------|
| 00 | [Agent Context Gathering](pipeline/00_agent-context-gathering.md) | Pre-stage | Retrieve and compress relevant codebase files |
| 01 | [Implementation Check](pipeline/01_implementation-check.md) | Pre-stage | Detect if the task is already built |
| 02 | [Context Stage](pipeline/02_context-stage.md) | Stage 1 | Extract requirements, constraints, assumptions |
| 03 | [Architecture + Design Stage](pipeline/03_architecture-design-stage.md) | Stage 2 | Explore approaches, produce ADRs, components, artifacts |
| 04 | [Roadmap + Risk Stage](pipeline/04_roadmap-risk-stage.md) | Stage 3 | Build phased plan with risk assessment |
| 05 | [Coherence Check](pipeline/05_coherence-check.md) | Post-stage | Cross-stage consistency verification |
| 06 | [Confidence Scoring](pipeline/06_confidence-scoring.md) | Post-stage | Per-section quality assessment |

## Infrastructure

Cross-cutting concerns that support the pipeline.

| Doc | Feature | Description |
|-----|---------|-------------|
| [Per-Field Extraction](infrastructure/per-field-extraction.md) | Extraction | How small models produce reliable structured output |
| [Split Reasoning](infrastructure/split-reasoning.md) | Context management | Reducing peak context for smaller models |
| [Crash Recovery](infrastructure/crash-recovery.md) | Reliability | Checkpoint-based pipeline resumption |
| [LLM Providers](infrastructure/llm-providers.md) | Abstraction | Ollama, LM Studio, llama.cpp backends |
| [Verification Agents](infrastructure/verification-agents.md) | Quality | 5 post-reasoning agents that catch architectural flaws |
| [Grounding Validation](infrastructure/grounding-validation.md) | Quality | AST + LLM validation of generated artifacts |
