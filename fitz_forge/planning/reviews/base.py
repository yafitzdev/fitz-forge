# fitz_forge/planning/reviews/base.py
"""Shared types for every review pass.

A review consumes one stage's output + relevant context and returns a
``ReviewResult`` of ``ReviewIssue``s. Every review uses the same shape
so the orchestrator, the CLI, and future UIs can log and route them
uniformly regardless of which stage emitted them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReviewIssue:
    """A specific critique surfaced by a review pass.

    Fields mirror how a senior engineer writes a design-review comment:
    what they expected, what they saw, and the concrete change they'd
    ask for. All string-valued so any review can populate them without
    leaking stage-specific types.
    """

    scope: str
    """The review's stage (``"decomposition"``, ``"artifact"``, ...).
    Useful when multiple reviews' issues are aggregated."""

    target: str
    """Identifier of the thing being critiqued — a decision ID (``"d1"``),
    a file path (``"engine.py"``), a file:line pair, or the literal
    ``"missing"`` when the issue is about something absent."""

    intent: str
    """What the senior engineer expected — the quality bar, not a
    verbatim taxonomy quote."""

    actual: str
    """What is currently there — the observed shortfall."""

    suggestion: str
    """Concrete change to close the gap. Phrased as an instruction
    the next generation pass can act on directly."""


@dataclass
class ReviewResult:
    """Outcome of one review pass over a single stage's output."""

    scope: str
    passed: bool
    issues: list[ReviewIssue] = field(default_factory=list)
    raw_response: str = ""

    @property
    def issue_count(self) -> int:
        return len(self.issues)


def format_issues_feedback(issues: list[ReviewIssue]) -> str:
    """Render a human-readable feedback block for retry prompts.

    Used when a reviewer finds issues and the caller regenerates with
    feedback. Any stage's retry prompt can concatenate this block to
    tell the model what the senior engineer critiqued. Empty when no
    issues — safe to unconditionally append.
    """
    if not issues:
        return ""
    lines = ["## Review feedback (address each item)", ""]
    for issue in issues:
        lines.append(f"- **{issue.target}** — {issue.intent}")
        lines.append(f"  - current: {issue.actual}")
        lines.append(f"  - required: {issue.suggestion}")
    return "\n".join(lines)
