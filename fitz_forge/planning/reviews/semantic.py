# fitz_forge/planning/reviews/semantic.py
"""Senior-engineer review of generated artifacts against design intent.

After per-artifact generation closes at the parse/shape level, ask the
LLM whether the produced code delivers what the synthesis reasoning
called for. Any contradictions between intent and code come back as
``ReviewIssue``s which the artifact-generation repair loop routes
into per-file regeneration.

Follows the composable ``reviews/`` pattern: shared ``ReviewResult``
and ``ReviewIssue`` types from ``base.py``; scope tag is
``"artifact"``; each issue's ``target`` encodes ``file:line``. The
LLM still produces the richer ``file`` / ``line`` / ``fix`` fields
because those match how code review reads — they're mapped onto the
unified shape internally so external callers see the same type every
review emits.

Language-agnostic by construction: the gate operates on file contents
and design text, not AST shape. The same gate runs over Python and
TypeScript artifact sets.
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
    "You are a senior software engineer reviewing generated code against "
    "the design intent it was supposed to deliver. Your job: find places "
    "where the implementation contradicts the design. You are NOT judging "
    "prose or style — you are checking whether the code actually does what "
    "the design called for."
)


def _format_decisions(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return "(no decisions recorded)"
    lines: list[str] = []
    for i, d in enumerate(decisions, 1):
        did = d.get("decision_id", f"d{i}")
        decision_text = d.get("decision", "")
        lines.append(f"- **{did}**: {decision_text}")
        constraints = d.get("constraints_for_downstream") or []
        for c in constraints:
            lines.append(f"    - constraint: {c}")
    return "\n".join(lines)


def _format_artifacts(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "(no artifacts produced)"
    parts: list[str] = []
    for a in artifacts:
        filename = a.get("filename", "<unknown>")
        content = a.get("content", "") or ""
        parts.append(f"### {filename}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _build_user_prompt(
    reasoning: str,
    decisions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> str:
    return (
        "## Design Intent (synthesis reasoning)\n"
        f"{reasoning.strip() or '(none)'}\n\n"
        "## Resolved Decisions\n"
        f"{_format_decisions(decisions)}\n\n"
        "## Artifacts as Produced\n"
        f"{_format_artifacts(artifacts)}\n\n"
        "## Your Task\n\n"
        "For every place where the implementation contradicts the design "
        "intent, emit one issue. Examples of what to look for:\n\n"
        "- The design says \"stream end-to-end\" but a method calls a blocking "
        "sibling and yields the full result once.\n"
        "- Method named in one artifact does not exist (or has a different "
        "name) on the class it's called on in another artifact.\n"
        "- A layer the design called out is skipped (e.g. design says "
        "route -> service -> engine, code goes route -> engine direct).\n"
        "- A streaming variant is defined in one artifact but a streaming "
        "caller uses the blocking variant.\n\n"
        "Return JSON only. No prose. No code fences.\n\n"
        "{\n"
        "  \"passed\": true | false,\n"
        "  \"issues\": [\n"
        "    {\n"
        "      \"file\": \"<artifact filename>\",\n"
        "      \"line\": <approximate line number, integer>,\n"
        "      \"intent\": \"<what the design called for, in your own words>\",\n"
        "      \"actual\": \"<what the code does>\",\n"
        "      \"suggestion\": \"<concrete change needed to make code match intent>\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "If the code matches the design, return {\"passed\": true, "
        "\"issues\": []}. Do not manufacture issues for code that is "
        "already correct — that just burns regeneration budget."
    )


def _parse_issue(raw: dict[str, Any]) -> ReviewIssue | None:
    """Map an LLM-emitted issue (with file/line fields) to the unified shape.

    Artifact-review issues want file + line specifically because that's
    what a code-review note looks like. The unified ``ReviewIssue``
    shape has a single ``target`` field; we compose ``file:line`` when
    a line is present, or fall back to the bare filename.

    Accepts both the modern ``suggestion`` key and the legacy ``fix``
    key for the change field — makes the prompt's output more forgiving
    to models that drift between the two.
    """
    file = raw.get("file") or raw.get("target")
    intent = raw.get("intent")
    actual = raw.get("actual")
    suggestion = raw.get("suggestion") or raw.get("fix")
    if not (isinstance(file, str) and file):
        return None
    if not (isinstance(intent, str) and isinstance(actual, str) and isinstance(suggestion, str)):
        return None
    if not (intent and actual and suggestion):
        return None
    line_raw = raw.get("line", 0)
    try:
        line = int(line_raw)
    except (TypeError, ValueError):
        line = 0

    # File:line target lets format_issues_feedback render a single
    # coordinate string consistent with the other reviews.
    if ":" in file:
        target = file  # model already composed it
    elif line:
        target = f"{file}:{line}"
    else:
        target = file

    return ReviewIssue(
        scope="artifact",
        target=target,
        intent=intent,
        actual=actual,
        suggestion=suggestion,
    )


async def review_artifacts(
    reasoning: str,
    decisions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    client: Any,
    *,
    max_tokens: int = 4096,
    label: str = "semantic_review",
) -> ReviewResult:
    """Run one senior-engineer critique of the generated artifacts.

    Returns a ``ReviewResult`` with scope ``"artifact"``. On unparseable
    output, returns ``passed=True`` so the repair loop cannot spin on
    a broken LLM response.
    """
    if not artifacts:
        return ReviewResult(scope="artifact", passed=True)

    user_prompt = _build_user_prompt(reasoning, decisions, artifacts)
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
        logger.warning(
            "semantic_review: could not parse response (%s); treating as passed", e
        )
        return ReviewResult(scope="artifact", passed=True, raw_response=raw)

    if not isinstance(parsed, dict):
        logger.warning(
            "semantic_review: response is not a JSON object (got %s); treating as passed",
            type(parsed).__name__,
        )
        return ReviewResult(scope="artifact", passed=True, raw_response=raw)

    # Accept both the current ``passed`` / ``issues`` schema and the
    # historical ``matches_intent`` / ``discrepancies`` shape so models
    # trained on the older prompt don't break the gate.
    passed_raw = parsed.get("passed")
    if passed_raw is None:
        passed_raw = parsed.get("matches_intent", True)
    passed = bool(passed_raw)

    raw_issues = parsed.get("issues")
    if raw_issues is None:
        raw_issues = parsed.get("discrepancies") or []

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
        scope="artifact",
        passed=passed,
        issues=issues,
        raw_response=raw,
    )


# Backwards-compat alias for pre-unification callers.
semantic_review = review_artifacts
