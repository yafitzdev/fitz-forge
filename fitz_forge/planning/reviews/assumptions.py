# fitz_forge/planning/reviews/assumptions.py
"""Senior-engineer adversarial review of recorded assumptions.

The context stage produces an ``assumptions`` list — places the model
had to guess because the task description and codebase didn't pin
things down. Every assumption is a potential time bomb: if it's false,
every downstream decision built on it inherits the defect. A senior
engineer in a design review won't just read the assumption list; they
will actively challenge each entry against the code.

This review asks, for each assumption:

    * Is it actually true in the codebase? (search for contradicting
      evidence)
    * Is it unverified — plausible but the code hasn't been checked?
    * Is there a specific file that proves/disproves it?

Output is a list of ``ReviewIssue``s for assumptions that are
contradicted or unverifiable. The caller surfaces these as plan
diagnostics so the downstream coder sees which assumptions to
double-check before acting.

Language and codebase agnostic: operates on assumption text +
codebase context; no language-specific parsing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fitz_forge.llm.generate import generate
from fitz_forge.planning.pipeline.stages.base import extract_json

from .base import ReviewIssue, ReviewResult

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a senior software engineer reviewing assumptions a junior "
    "engineer recorded while planning a task. The junior had to guess "
    "because the task description and codebase didn't pin things down. "
    "Your job is to challenge each assumption against the codebase "
    "context and flag any that are demonstrably wrong or that the "
    "codebase contradicts. You are NOT flagging assumptions that are "
    "simply uncertain — only those you can show are incorrect or that "
    "the code has clear evidence against."
)


def _format_assumptions(assumptions: list[dict[str, Any]]) -> str:
    if not assumptions:
        return "(no assumptions recorded)"
    lines: list[str] = []
    for i, a in enumerate(assumptions, 1):
        if not isinstance(a, dict):
            continue
        text = a.get("assumption", "")
        impact = a.get("impact", "")
        confidence = a.get("confidence", "")
        lines.append(f"### Assumption {i}")
        lines.append(f"**Statement:** {text}")
        if impact:
            lines.append(f"**Impact if wrong:** {impact}")
        if confidence:
            lines.append(f"**Confidence:** {confidence}")
        lines.append("")
    return "\n".join(lines)


def _build_user_prompt(
    task_description: str,
    assumptions: list[dict[str, Any]],
    gathered_context: str,
) -> str:
    context_preview = gathered_context[:6000]
    if len(gathered_context) > 6000:
        context_preview += "\n…(truncated)…"
    return (
        f"## Task\n\n{task_description}\n\n"
        "## Recorded Assumptions\n\n"
        f"{_format_assumptions(assumptions)}\n\n"
        "## Codebase Context (ground truth)\n\n"
        f"{context_preview or '(none)'}\n\n"
        "## Your Task\n\n"
        "For each assumption, decide:\n\n"
        "- **CONTRADICTED**: the codebase contains direct evidence "
        "against the assumption. Flag these — they must be corrected "
        "before planning proceeds or the plan is built on a false "
        "premise.\n"
        "- **UNVERIFIED**: the codebase doesn't contain evidence "
        "either way. Flag only if the impact-if-wrong is high; "
        "otherwise let it through (some uncertainty is fine).\n"
        "- **SUPPORTED**: the codebase contains evidence matching the "
        "assumption. Do not flag.\n\n"
        "Only emit issues for CONTRADICTED and high-impact UNVERIFIED "
        "cases. Quote the specific file name / class / signature that "
        "proves the contradiction in your `actual` text. If no codebase "
        "evidence exists to judge any assumption, return "
        '{"passed": true, "issues": []}.\n\n'
        "Return JSON only. No prose. No code fences.\n\n"
        "{\n"
        '  "passed": true | false,\n'
        '  "issues": [\n'
        "    {\n"
        '      "target": "assumption 1" | "assumption 2" | ...,\n'
        '      "intent": "<what the junior assumed>",\n'
        '      "actual": "<codebase evidence against it, with a specific file/class/signature reference>",\n'
        '      "suggestion": "<how to correct the assumption or which downstream decision to revisit>"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Be strict about only flagging assumptions you can disprove or "
        "that would derail the plan if wrong. Noise here is expensive."
    )


def _parse_issue(raw: dict[str, Any]) -> ReviewIssue | None:
    target = raw.get("target")
    intent = raw.get("intent")
    actual = raw.get("actual")
    suggestion = raw.get("suggestion")
    if not all(isinstance(x, str) for x in (target, intent, actual, suggestion)):
        return None
    if not (target and intent and actual and suggestion):
        return None
    return ReviewIssue(
        scope="assumption",
        target=target,
        intent=intent,
        actual=actual,
        suggestion=suggestion,
    )


async def review_assumptions(
    task_description: str,
    assumptions: list[dict[str, Any]],
    client: Any,
    *,
    gathered_context: str = "",
    max_tokens: int = 4096,
    label: str = "assumption_review",
) -> ReviewResult:
    """Run one senior-engineer adversarial pass over the assumption list.

    Returns a ReviewResult with scope ``"assumption"``. On unparseable
    or malformed output, returns ``passed=True`` so the MVP stays
    purely additive — detection without blocking the pipeline.
    """
    if not assumptions:
        return ReviewResult(scope="assumption", passed=True)

    user_prompt = _build_user_prompt(
        task_description=task_description,
        assumptions=assumptions,
        gathered_context=gathered_context,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw = await generate(
        client,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
        label=label,
    )

    try:
        parsed = extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("assumption_review: unparseable response (%s); treating as passed", e)
        return ReviewResult(scope="assumption", passed=True, raw_response=raw)

    if not isinstance(parsed, dict):
        logger.warning(
            "assumption_review: non-object response (%s); treating as passed",
            type(parsed).__name__,
        )
        return ReviewResult(scope="assumption", passed=True, raw_response=raw)

    passed = bool(parsed.get("passed", True))
    raw_issues = parsed.get("issues") or []
    issues: list[ReviewIssue] = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            issue = _parse_issue(item)
            if issue is not None:
                issues.append(issue)

    if issues and passed:
        passed = False
    if not issues and not passed:
        passed = True

    return ReviewResult(
        scope="assumption",
        passed=passed,
        issues=issues,
        raw_response=raw,
    )
