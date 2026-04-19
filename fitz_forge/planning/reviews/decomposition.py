# fitz_forge/planning/reviews/decomposition.py
"""Senior-engineer critique of the decision decomposition.

The decomposer produces a list of atomic questions that the resolver
will answer one at a time. If those questions are the wrong shape —
pre-committing to a mechanism, skipping a critical concern, or
contradicting each other — every downstream stage inherits the
defect and the plan's ceiling is set there.

A senior engineer reviewing a junior's decomposition asks:

    * Does each pattern decision really evaluate alternatives, or is
      it a disguised interface decision that has already picked the
      mechanism?
    * Do downstream decisions depend on a pattern decision but
      pre-commit to one branch, making them inconsistent if the
      pattern goes the other way?
    * Is any critical question missing that an experienced engineer
      would insist on (migration / rollout / auth / testing / backwards
      compat / concurrency — pick whichever applies to the task)?
    * Are two decisions redundant — asking the same thing twice with
      different wording?
    * For tasks that change an entry point, does the decomposition
      trace the full call chain from the entry to the implementation,
      or does it skip a middle layer?

This review fires after the decomposer picks its best candidate and
before resolution begins. When issues exist, the caller regenerates
the decomposition with the review feedback injected.

Language and codebase agnostic: operates on the task description,
call graph, and proposed decisions — no AST, no language-specific
patterns. Same review runs over Python, TypeScript, Go, etc.
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
    "decomposition of a planning task into atomic decisions. Your job is "
    "to find places where the question list would derail the downstream "
    "plan — questions that pre-commit to a mechanism before evaluating "
    "alternatives, critical questions that are missing, decisions that "
    "depend on each other but contradict, or layers in the call chain "
    "that have been skipped. You are NOT judging the resolver's answers "
    "— those come later. You are judging whether these are the right "
    "questions to ask."
)


def _format_decisions(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return "(no decisions produced)"
    lines: list[str] = []
    for d in decisions:
        did = d.get("id", "?")
        category = d.get("category", "?")
        question = d.get("question", "")
        deps = d.get("depends_on") or []
        relevant = d.get("relevant_files") or []
        lines.append(f"### {did} ({category})")
        lines.append(f"**Q:** {question}")
        if deps:
            lines.append(f"**Depends on:** {', '.join(deps)}")
        if relevant:
            lines.append(f"**Relevant files:** {', '.join(relevant)}")
        lines.append("")
    return "\n".join(lines)


def _build_user_prompt(
    task_description: str,
    decisions: list[dict[str, Any]],
    call_graph_text: str,
    file_manifest: str,
    rubric_hints: str | None,
) -> str:
    rubric_block = ""
    if rubric_hints and rubric_hints.strip():
        rubric_block = (
            "## Quality Criteria (domain expectations)\n\n"
            f"{rubric_hints.strip()}\n\n"
            "Use these when deciding whether any critical question is "
            "missing from the decomposition.\n\n"
        )
    return (
        f"## Task\n\n{task_description}\n\n"
        f"{call_graph_text or '(no call graph)'}\n\n"
        f"{file_manifest or '(no file manifest)'}\n\n"
        f"{rubric_block}"
        "## Proposed Decomposition\n\n"
        f"{_format_decisions(decisions)}\n\n"
        "## Your Task\n\n"
        "Critique the proposed decomposition. Look specifically for:\n\n"
        "1. **Disguised interface decisions.** A PATTERN decision must "
        "evaluate alternatives (e.g. 'new module vs extend existing'). "
        "If a pattern question names a specific mechanism in the "
        "question itself ('How should we extend X...'), the mechanism "
        "was picked before the evaluation — flag it.\n\n"
        "2. **Downstream pre-commitment.** Any decision whose "
        "`depends_on` includes a pattern decision must phrase itself "
        "so it can be answered *either way* the pattern goes. "
        "'If extending X, what fields...' is a failure shape — the "
        "resolver will answer the 'if extending' branch even when the "
        "pattern picked the alternative, producing a Frankenstein plan.\n\n"
        "3. **Missing critical questions.** If the task introduces new "
        "behavior at an API boundary, is there a question about "
        "authentication / authorization? For a new schema, is there a "
        "migration question? For a task with concurrency implications, "
        "is there a question about locking / ordering / idempotence? "
        "Only flag a missing question when its absence would genuinely "
        "derail implementation.\n\n"
        "4. **Redundant decisions.** Two questions asking the same "
        "thing with different wording. Merge candidates.\n\n"
        "5. **Call-chain gaps.** When the task adds behavior at an "
        "entry point, a decision must exist for each layer between the "
        "entry and the implementation. Flag any missing middle layer.\n\n"
        "Return JSON only. No prose. No code fences.\n\n"
        "{\n"
        "  \"passed\": true | false,\n"
        "  \"issues\": [\n"
        "    {\n"
        "      \"target\": \"<decision id, or 'missing' if the issue is a missing question>\",\n"
        "      \"intent\": \"<the quality bar a senior engineer would expect>\",\n"
        "      \"actual\": \"<what this decomposition currently does wrong>\",\n"
        "      \"suggestion\": \"<concrete change: rephrase this question, add a new decision, merge two decisions, etc.>\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "If you find no genuine issues (the decomposition is sound), "
        "return {\"passed\": true, \"issues\": []}. Do not manufacture "
        "issues for decompositions that are already good — that just "
        "makes the plan slower without improving quality."
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
        scope="decomposition",
        target=target,
        intent=intent,
        actual=actual,
        suggestion=suggestion,
    )


async def review_decomposition(
    task_description: str,
    decisions: list[dict[str, Any]],
    client: Any,
    *,
    call_graph_text: str = "",
    file_manifest: str = "",
    rubric_hints: str | None = None,
    max_tokens: int = 4096,
    label: str = "decomposition_review",
) -> ReviewResult:
    """Run one senior-engineer critique of the proposed decomposition.

    Returns a ``ReviewResult`` with scope ``"decomposition"``. On
    unparseable or malformed output, returns ``passed=True`` so the
    retry loop cannot spin on a broken LLM response.
    """
    if not decisions:
        return ReviewResult(scope="decomposition", passed=True)

    user_prompt = _build_user_prompt(
        task_description=task_description,
        decisions=decisions,
        call_graph_text=call_graph_text,
        file_manifest=file_manifest,
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
            "decomposition_review: unparseable response (%s); treating as passed",
            e,
        )
        return ReviewResult(scope="decomposition", passed=True, raw_response=raw)

    if not isinstance(parsed, dict):
        logger.warning(
            "decomposition_review: non-object response (%s); treating as passed",
            type(parsed).__name__,
        )
        return ReviewResult(scope="decomposition", passed=True, raw_response=raw)

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

    # Normalise: passed must agree with the issues list.
    if issues and passed:
        passed = False
    if not issues and not passed:
        passed = True

    return ReviewResult(
        scope="decomposition",
        passed=passed,
        issues=issues,
        raw_response=raw,
    )
