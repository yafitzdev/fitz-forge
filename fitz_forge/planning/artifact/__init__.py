# fitz_forge/planning/artifact/__init__.py
"""Artifact generation black box — validated code artifacts from LLM."""

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
    "ArtifactResult",
    "ArtifactSetResult",
    "ArtifactError",
    "ClosureViolation",
    "SymbolRef",
    "assemble_context",
    "check_closure",
    "extract_provides",
    "extract_references",
    "generate_artifact",
    "generate_artifact_set",
    "validate",
]
