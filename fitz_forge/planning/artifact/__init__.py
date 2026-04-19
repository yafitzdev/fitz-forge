# fitz_forge/planning/artifact/__init__.py
"""Artifact generation black box — validated code artifacts from LLM."""

from fitz_forge.planning.reviews import ReviewIssue, ReviewResult
from fitz_forge.planning.reviews import review_artifacts as semantic_review

from .closure import (
    ClosureViolation,
    SymbolRef,
    check_closure,
    extract_provides,
    extract_references,
)
from .context import ArtifactContext, assemble_context
from .generator import (
    ArtifactResult,
    ArtifactSetResult,
    generate_artifact,
    generate_artifact_set,
)
from .validate import ArtifactError, validate

__all__ = [
    "ArtifactContext",
    "ArtifactError",
    "ArtifactResult",
    "ArtifactSetResult",
    "ClosureViolation",
    "ReviewIssue",
    "ReviewResult",
    "SymbolRef",
    "assemble_context",
    "check_closure",
    "extract_provides",
    "extract_references",
    "generate_artifact",
    "generate_artifact_set",
    "semantic_review",
    "validate",
]
