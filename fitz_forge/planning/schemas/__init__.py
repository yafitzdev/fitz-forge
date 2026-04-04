# fitz_forge/planning/schemas/__init__.py
"""Pydantic schemas for structured output from each planning stage."""

from fitz_forge.planning.schemas.architecture import Approach, ArchitectureOutput
from fitz_forge.planning.schemas.context import Assumption, ContextOutput
from fitz_forge.planning.schemas.design import ADR, Artifact, ComponentDesign, DesignOutput
from fitz_forge.planning.schemas.plan_output import PlanOutput
from fitz_forge.planning.schemas.risk import Risk, RiskOutput
from fitz_forge.planning.schemas.roadmap import Phase, PhaseRef, RoadmapOutput

__all__ = [
    "Assumption",
    "ContextOutput",
    "ArchitectureOutput",
    "Approach",
    "DesignOutput",
    "ADR",
    "Artifact",
    "ComponentDesign",
    "RoadmapOutput",
    "Phase",
    "PhaseRef",
    "RiskOutput",
    "Risk",
    "PlanOutput",
]
