# fitz_forge/models/events.py
"""
Plan execution events.

Ephemeral event types emitted by the worker while a job runs. Consumed by
the CLI (and any future streaming MCP tool) to render a live feed. Not
persisted — SQLite remains the source of truth for durable job state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseChanged:
    """A stage transition inside the pipeline. Carries a human-readable description."""

    job_id: str
    progress: float
    phase: str
    description: str


@dataclass(frozen=True)
class JobCompleted:
    """Terminal event — job finished successfully.

    quality_score is rendered as ``N/{max_quality_score}`` when both are set.
    When ``quality_applicable=False`` the CLI renders it as an em-dash,
    signalling "not applicable" (e.g. short-circuit: task already implemented,
    no artifacts to score). When scoring raised an error but the plan otherwise
    succeeded, both fields are None and ``quality_error`` carries the message.
    """

    job_id: str
    file_path: str | None
    quality_score: float | None
    elapsed_s: float
    max_quality_score: int | None = None
    quality_applicable: bool = True
    quality_error: str | None = None


@dataclass(frozen=True)
class JobAwaitingReview:
    """Terminal (for this run) — pipeline paused waiting for API-review confirmation."""

    job_id: str
    elapsed_s: float


@dataclass(frozen=True)
class JobFailed:
    """Terminal event — job failed. Includes error text and elapsed time."""

    job_id: str
    error: str
    elapsed_s: float


PlanEvent = PhaseChanged | JobCompleted | JobAwaitingReview | JobFailed


_PHASE_DESCRIPTIONS: dict[str, str] = {
    "starting": "Starting...",
    "resuming": "Resuming from checkpoint...",
    "initializing": "Initializing...",
    "health_check": "Checking LLM connectivity...",
    "loading_model": "Loading model into memory...",
    "agent:mapping": "Mapping codebase...",
    "agent:expanding_query": "Expanding query with LLM...",
    "agent:structural_scan": "Scanning structural index...",
    "agent:bm25": "BM25 keyword search...",
    "agent:embedding": "Embedding search...",
    "agent:reranking": "Cross-encoder reranking...",
    "agent:import_expand": "Following imports...",
    "agent:neighbor_expand": "Expanding to neighboring files...",
    "agent:reading": "Reading source files...",
    "agent:screening": "BM25 screening...",
    "agent:confirming": "Confirming files with LLM...",
    "agent:selecting": "Selecting relevant files...",
    "agent:selecting_dirs": "Selecting relevant directories...",
    "agent:synthesizing": "Synthesizing context...",
    "agent:checking_existing": "Checking for existing implementation...",
    "agent_exploring_complete": "Codebase analysis complete",
    "context:reasoning": "Analyzing requirements and constraints...",
    "context_complete": "Requirements analysis complete",
    "architecture_design:reasoning": "Exploring architecture and design...",
    "architecture_design_complete": "Architecture+Design complete",
    "roadmap_risk:reasoning": "Planning roadmap and assessing risks...",
    "roadmap_risk_complete": "Roadmap+Risk complete",
    "coherence_check": "Checking cross-stage coherence...",
    "scoring": "Scoring plan quality...",
    "rendering": "Rendering plan to markdown...",
    "writing_file": "Saving plan to disk...",
    "finalizing": "Finalizing...",
    "awaiting_review_confirmation": "Awaiting API review confirmation",
    "executing_review": "Running API review...",
    "api_review_confirmed": "API review confirmed",
    "pending_engine": "Pipeline not configured",
}


def describe_phase(phase: str | None) -> str:
    """Map a pipeline phase string to a human-readable description."""
    if not phase:
        return ""
    if phase in _PHASE_DESCRIPTIONS:
        return _PHASE_DESCRIPTIONS[phase]
    if phase.startswith("agent:confirming:"):
        return f"Confirming {phase[len('agent:confirming:'):]}"
    if phase.startswith("agent:summarizing:"):
        filename = phase.split(":")[-1].rsplit("/", 1)[-1]
        return f"Summarizing {filename}..."
    if phase.endswith(":critiquing"):
        return "Reviewing analysis for quality..."
    if ":extracting:" in phase:
        return f"Extracting {phase.split(':')[-1]}..."
    if phase in ("context", "architecture_design", "roadmap_risk"):
        return _PHASE_DESCRIPTIONS.get(f"{phase}:reasoning", f"Working on {phase}...")
    return phase
