# fitz_forge/planning/artifact/semantic_review.py
"""Semantic-review gate.

After per-artifact generation closes at the parse / shape level, ask the
LLM whether the produced artifacts deliver the design intent that the
synthesis reasoning called for. The LLM returns a list of discrepancies
(``intent`` vs ``actual`` on a specific file + line, with a concrete
``fix``) which the caller routes back into the per-artifact regeneration
loop.

This replaces the family of shape-pattern closure invariants (B9/B11/B17)
that tried to encode "streaming methods must delegate to streaming
siblings", "bodies may not reference unbound names", etc. as tree-sitter
predicates. Those patterns kept narrowing at different sites (B15, B16,
B17) without addressing the underlying architectural gap: artifacts are
generated per-file in isolation, so set-level design intent ("engine
must call ``synthesizer.stream_query``, not the blocking variant") gets
lost between synthesis and code generation. The semantic gate reads the
whole set + intent and catches the contradiction directly.

Language-agnostic by construction: prompts operate on file contents and
design text; there's no AST shape matching. The same gate runs over
Python and TypeScript artifact sets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fitz_forge.llm.generate import generate
from fitz_forge.planning.pipeline.stages.base import extract_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Discrepancy:
    """One place where the implementation contradicts the design intent."""

    file: str
    line: int
    intent: str
    actual: str
    fix: str


@dataclass
class ReviewResult:
    """Outcome of one semantic-review pass over an artifact set."""

    matches_intent: bool
    discrepancies: list[Discrepancy] = field(default_factory=list)
    raw_response: str = ""


_SYSTEM_PROMPT = (
    "You are reviewing a software plan's artifacts against the design intent "
    "that produced them. Your job: find places where the implementation does "
    "not deliver what the design called for. You are NOT judging quality — "
    "you are checking for contradictions between intent and code."
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
        "intent, emit one discrepancy. Examples of what to look for:\n\n"
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
        "  \"matches_intent\": true | false,\n"
        "  \"discrepancies\": [\n"
        "    {\n"
        "      \"file\": \"<artifact filename>\",\n"
        "      \"line\": <approximate line number, integer>,\n"
        "      \"intent\": \"<what the design called for, in your own words>\",\n"
        "      \"actual\": \"<what the code does>\",\n"
        "      \"fix\": \"<concrete change needed to make code match intent>\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "If matches_intent is true, discrepancies must be empty. If you find "
        "no contradictions after a careful read, output "
        "{\"matches_intent\": true, \"discrepancies\": []}."
    )


def _parse_discrepancy(raw: dict[str, Any]) -> Discrepancy | None:
    file = raw.get("file")
    intent = raw.get("intent")
    actual = raw.get("actual")
    fix = raw.get("fix")
    if not (isinstance(file, str) and file):
        return None
    if not (isinstance(intent, str) and isinstance(actual, str) and isinstance(fix, str)):
        return None
    line_raw = raw.get("line", 0)
    try:
        line = int(line_raw)
    except (TypeError, ValueError):
        line = 0
    return Discrepancy(file=file, line=line, intent=intent, actual=actual, fix=fix)


async def semantic_review(
    reasoning: str,
    decisions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    client: Any,
    *,
    max_tokens: int = 4096,
    label: str = "semantic_review",
) -> ReviewResult:
    """Run one semantic-review pass. Returns discrepancies for the repair loop.

    On unparseable output the gate returns ``matches_intent=True`` so the
    caller's loop doesn't spin forever on a broken LLM response; the raw
    text is preserved for debugging.
    """
    if not artifacts:
        return ReviewResult(matches_intent=True, discrepancies=[], raw_response="")

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
            "semantic_review: could not parse response (%s); treating as matches_intent=True",
            e,
        )
        return ReviewResult(matches_intent=True, discrepancies=[], raw_response=raw)

    if not isinstance(parsed, dict):
        logger.warning(
            "semantic_review: response is not a JSON object (got %s); treating as matches_intent=True",
            type(parsed).__name__,
        )
        return ReviewResult(matches_intent=True, discrepancies=[], raw_response=raw)

    matches = bool(parsed.get("matches_intent", True))
    raw_discrepancies = parsed.get("discrepancies") or []
    discrepancies: list[Discrepancy] = []
    if isinstance(raw_discrepancies, list):
        for item in raw_discrepancies:
            if not isinstance(item, dict):
                continue
            d = _parse_discrepancy(item)
            if d is not None:
                discrepancies.append(d)

    # Normalise: matches_intent must agree with the list.
    if discrepancies and matches:
        matches = False
    if not discrepancies and not matches:
        matches = True

    return ReviewResult(
        matches_intent=matches,
        discrepancies=discrepancies,
        raw_response=raw,
    )


def format_feedback(discrepancies: list[Discrepancy]) -> str:
    """Render a human-readable feedback block for artifact regeneration."""
    if not discrepancies:
        return ""
    lines = ["## Semantic review feedback", ""]
    for d in discrepancies:
        lines.append(f"- line {d.line}")
        lines.append(f"  - design intent: {d.intent}")
        lines.append(f"  - current code: {d.actual}")
        lines.append(f"  - required change: {d.fix}")
    return "\n".join(lines)
