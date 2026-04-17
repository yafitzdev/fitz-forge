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


@dataclass(frozen=True)
class DecisionResolved:
    """A single atomic decision has been resolved.

    Carries the decision id, a short human-readable summary ('what did the
    model decide'), and the primary target file if any. Rendered by the CLI
    as a dim indented bullet under the stage progress line, e.g.::

        · d5: Add StreamingResponse route → fitz_sage/api/routes/collections.py
    """

    job_id: str
    decision_id: str
    summary: str
    target_file: str | None


@dataclass(frozen=True)
class PhaseEnter:
    """One of the 10 top-level pipeline phases has been entered.

    Rendered by the CLI as a highlighted banner (e.g. Rich title rule).
    Finer-grained sub-phases (stage substeps, agent: events, etc.) are
    demoted to indented dim bullets beneath the banner.
    """

    job_id: str
    phase_number: int
    phase_label: str


@dataclass(frozen=True)
class DecisionHallucinationDropped:
    """A piece of resolved-decision evidence was dropped as hallucinated.

    The stage detected a file reference in the evidence that does not exist
    in the known file set. Rendered as a dim indented bullet, e.g.::

        · d7: dropped hallucinated reference to fitz_sage/evaluation/schema.py
    """

    job_id: str
    decision_id: str
    evidence_snippet: str


PlanEvent = (
    PhaseChanged
    | JobCompleted
    | JobAwaitingReview
    | JobFailed
    | PhaseEnter
    | DecisionResolved
    | DecisionHallucinationDropped
)


# ---------------------------------------------------------------------------
# Top-level phase classification
# ---------------------------------------------------------------------------

# The 10 user-visible top-level phases, in order. Each value is (number, label).
TOP_LEVEL_PHASES: list[tuple[int, str]] = [
    (1, "Check connectivity"),
    (2, "Load model"),
    (3, "Gather codebase context"),
    (4, "Check existing implementation"),
    (5, "Extract call graph"),
    (6, "Decompose decisions"),
    (7, "Resolve decisions"),
    (8, "Synthesize plan"),
    (9, "Ground & check coherence"),
    (10, "Render & save"),
]


def classify_phase(phase: str | None) -> int | None:
    """Map a low-level pipeline phase string to its top-level phase number.

    Used by the worker to decide whether a PhaseEnter banner needs emitting
    (i.e. has the top-level phase changed?) before the next PhaseChanged.

    Returns None for phases that don't belong to any of the 10 top-level
    phases (e.g. "starting", "resuming", "initializing" — meta states).
    """
    if not phase:
        return None

    # Exact matches first (faster, unambiguous)
    exact_map = {
        "health_check": 1,
        "loading_model": 2,
        "agent_exploring_complete": 3,  # in case agent phase is skipped due to resume
        "agent:checking_existing": 4,
        "call_graph_extraction": 5,
        "decision_decomposition": 6,
        "decision_decomposition_complete": 6,
        "decision_resolution": 7,
        "decision_resolution_complete": 7,
        "synthesis": 8,
        "synthesis_complete": 8,
        "grounding_validation": 9,
        "coherence_check": 9,
        "rendering": 10,
        "writing_file": 10,
        "finalizing": 10,
    }
    if phase in exact_map:
        return exact_map[phase]

    # Prefix matches — any agent:* substep that isn't checking_existing is phase 3
    if phase.startswith("agent:"):
        return 3
    if phase.startswith("decision_decomposition:"):
        return 6
    if phase.startswith("decision_resolution:"):
        return 7
    if phase.startswith("synthesis:"):
        return 8

    return None


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
