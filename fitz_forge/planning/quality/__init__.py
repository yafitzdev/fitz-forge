# fitz_forge/planning/quality/__init__.py
"""Runtime quality indicators for a finalized plan.

Four deterministic dimensions surfaced to the user alongside the plan —
no LLM calls, no API costs, no offline evaluation harness. Consistent
with the evaluation framework in ``docs/SCORER-V2-SPEC.md`` but scoped
to what can be computed locally without a task taxonomy.
"""

from .indicators import (
    QualityIndicators,
    compute_quality_indicators,
    format_indicators_markdown,
)

__all__ = [
    "QualityIndicators",
    "compute_quality_indicators",
    "format_indicators_markdown",
]
