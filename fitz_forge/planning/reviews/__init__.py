# fitz_forge/planning/reviews/__init__.py
"""Composable LLM review passes for the planning pipeline.

Each review is a narrow LLM critique — "what would a senior engineer
say about this specific piece of the plan?" — scoped to one stage
output and returning a uniform ``ReviewResult``. Pipeline stages call
into the review they need at the point where it is relevant:

    review_decomposition  — after decision_decomposition emits questions
    review_artifacts      — after artifact generation emits code

All reviews share one result shape so the orchestrator can log and
route them uniformly and new reviews (architecture, assumption, risk)
can be added without changing call sites.

When no issues are found, ``ReviewResult.passed`` is ``True`` and the
caller proceeds. When issues exist, the caller surfaces them as
feedback and regenerates the relevant stage output.
"""

from .architecture import review_architecture
from .assumptions import review_assumptions
from .base import ReviewIssue, ReviewResult, format_issues_feedback
from .decomposition import review_decomposition
from .design import review_design
from .semantic import Discrepancy, format_feedback, review_artifacts

__all__ = [
    "Discrepancy",
    "ReviewIssue",
    "ReviewResult",
    "format_feedback",
    "format_issues_feedback",
    "review_architecture",
    "review_artifacts",
    "review_assumptions",
    "review_decomposition",
    "review_design",
]
