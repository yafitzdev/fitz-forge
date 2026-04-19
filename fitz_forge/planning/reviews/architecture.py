# fitz_forge/planning/reviews/architecture.py
"""Senior-engineer critique of the chosen architecture recommendation.

After synthesis extracts the architecture section (recommended
approach, reasoning, considered alternatives, key tradeoffs), this
review asks whether a senior engineer would actually endorse the
recommendation. Catches the class of failure where the model picks a
structurally inferior approach (e.g. "buffer the full answer and
split into fake chunks" when the task asks for real streaming) but
then writes an internally consistent plan for it — semantic-review
and decomposition review can't catch that because the internal
coherence is fine; only the pick itself is wrong.

A senior engineer reviewing the architecture asks:

    * Does the recommendation actually solve the task, or does it
      pay lip service while skipping the hard part? (E.g. wrapping
      a blocking call and yielding the result once isn't streaming.)
    * Do the listed alternatives include the real best option, or
      did the model reject two straw-men and pick the third?
    * Does the recommendation match the codebase's idioms? (E.g.
      proposing a new service on a codebase that uses composition,
      or a parallel class hierarchy on a codebase that uses mixins.)
    * Are the quality criteria in the rubric (if supplied) reflected
      in the choice?

Language and codebase agnostic: operates on the task description,
chosen architecture fields, and optional rubric hints. Same review
runs over Python, TypeScript, Go, etc.
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
    "You are a senior software engineer reviewing a junior engineer's "
    "architectural recommendation. Your job is to catch places where "
    "the recommendation doesn't actually solve the task, the listed "
    "alternatives are straw-men, or the chosen approach fights the "
    "codebase's existing idioms. You are NOT judging the quality of "
    "the prose — you are judging whether the RIGHT approach was picked."
)


def _format_approaches(approaches: list[dict[str, Any]]) -> str:
    if not approaches:
        return "(no alternatives recorded)"
    lines: list[str] = []
    for i, a in enumerate(approaches, 1):
        if not isinstance(a, dict):
            continue
        name = a.get("name", f"Approach {i}")
        description = a.get("description", "")
        lines.append(f"- **{name}**: {description}")
    return "\n".join(lines) if lines else "(no alternatives recorded)"


def _build_user_prompt(
    task_description: str,
    recommended: str,
    reasoning: str,
    approaches: list[dict[str, Any]],
    key_tradeoffs: str,
    gathered_context: str,
    rubric_hints: str | None,
) -> str:
    rubric_block = ""
    if rubric_hints and rubric_hints.strip():
        rubric_block = (
            "## Quality Criteria (domain expectations)\n\n"
            f"{rubric_hints.strip()}\n\n"
            "Flag the recommendation if it does not satisfy these.\n\n"
        )
    # Keep codebase context bounded so the review stays fast.
    context_preview = gathered_context[:4000]
    if len(gathered_context) > 4000:
        context_preview += "\n…(truncated)…"
    return (
        f"## Task\n\n{task_description}\n\n"
        "## Chosen Recommendation\n\n"
        f"**Recommended approach:** {recommended or '(empty)'}\n\n"
        f"**Reasoning:**\n{reasoning or '(empty)'}\n\n"
        "## Considered Alternatives\n\n"
        f"{_format_approaches(approaches)}\n\n"
        f"## Key Tradeoffs\n\n{key_tradeoffs or '(none recorded)'}\n\n"
        "## Codebase Context (for idiom check)\n\n"
        f"{context_preview or '(none)'}\n\n"
        f"{rubric_block}"
        "## Your Task\n\n"
        "Critique the architectural recommendation. Look specifically for:\n\n"
        "1. **Recommendation doesn't solve the task.** The most common "
        "failure: the approach looks plausible but quietly skips the "
        "hard part. Examples: \"add streaming\" by buffering the full "
        "response and splitting into fake tokens; \"add caching\" with "
        "no invalidation; \"add auth\" that checks a flag but doesn't "
        "verify signatures. If the recommendation is shaped like the "
        "solution but misses the *mechanism*, flag it.\n\n"
        "2. **Straw-man alternatives.** If only two approaches were "
        "considered and one is obviously broken, the comparison is "
        "not a real decision. Flag cases where a reasonable third "
        "alternative is missing.\n\n"
        "3. **Fights the codebase idioms.** If the codebase uses "
        "composition and the recommendation introduces a parallel "
        "inheritance hierarchy, or vice versa, flag it. The codebase "
        "context above is the ground truth for \"how this team builds "
        "things.\"\n\n"
        "4. **Rubric mismatch.** If quality criteria are supplied, "
        "any divergence from them is a flag — the recommendation "
        "should visibly aim at the criteria, not ignore them.\n\n"
        "Return JSON only. No prose. No code fences.\n\n"
        "{\n"
        "  \"passed\": true | false,\n"
        "  \"issues\": [\n"
        "    {\n"
        "      \"target\": \"architecture.recommended\" | \"architecture.approaches\" | \"architecture.reasoning\",\n"
        "      \"intent\": \"<what a senior engineer would expect>\",\n"
        "      \"actual\": \"<what this recommendation currently does wrong>\",\n"
        "      \"suggestion\": \"<concrete change: pick approach X, add alternative Y, rewrite reasoning to address Z>\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "If the recommendation is sound and the alternatives are "
        "realistic, return {\"passed\": true, \"issues\": []}. Do NOT "
        "manufacture issues for recommendations that are already good "
        "— that just burns plan time without improving quality."
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
        scope="architecture",
        target=target,
        intent=intent,
        actual=actual,
        suggestion=suggestion,
    )


async def review_architecture(
    task_description: str,
    architecture: dict[str, Any],
    client: Any,
    *,
    gathered_context: str = "",
    rubric_hints: str | None = None,
    max_tokens: int = 4096,
    label: str = "architecture_review",
) -> ReviewResult:
    """Run one senior-engineer critique of the chosen architecture.

    Input ``architecture`` is the typed dict produced by synthesis
    (``recommended``, ``reasoning``, ``approaches``, ``key_tradeoffs``
    keys). On unparseable output the review returns ``passed=True``
    so the retry loop cannot spin on a broken LLM response.
    """
    if not architecture or not architecture.get("recommended"):
        return ReviewResult(scope="architecture", passed=True)

    user_prompt = _build_user_prompt(
        task_description=task_description,
        recommended=str(architecture.get("recommended", "")),
        reasoning=str(architecture.get("reasoning", "")),
        approaches=architecture.get("approaches") or [],
        key_tradeoffs=str(architecture.get("key_tradeoffs", "")),
        gathered_context=gathered_context,
        rubric_hints=rubric_hints,
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
        logger.warning(
            "architecture_review: unparseable response (%s); treating as passed", e
        )
        return ReviewResult(scope="architecture", passed=True, raw_response=raw)

    if not isinstance(parsed, dict):
        logger.warning(
            "architecture_review: non-object response (%s); treating as passed",
            type(parsed).__name__,
        )
        return ReviewResult(scope="architecture", passed=True, raw_response=raw)

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
        scope="architecture",
        passed=passed,
        issues=issues,
        raw_response=raw,
    )
