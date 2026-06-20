# fitz_forge/planning/reviews/design.py
"""Senior-engineer critique of the design section.

The design section specifies component interfaces, data models, and
artifact responsibilities — the last plan-level detail before code
generation. Under-specified designs are the main reason plans land at
taxonomy-mid tiers (K2, RR2, S2) instead of the top tier: the design
says "record ranking signals" without enumerating
(base_score, strategy_weight, entity_bonus, keyword_boost, composite)
and the code generation settles for a single dict field because
nothing in the design demanded the breakdown.

A senior engineer reading a design document asks:

    * Are the component interfaces specific enough to produce the
      right code? (Method names, parameter lists, return types.)
    * Is the data model precise about field names? If rubric / domain
      criteria demand "preserve pre_rerank_score," is that literal
      field name in the design?
    * Are cross-component contracts type-consistent? (No leaky dicts
      where typed records are called for; no stringly-typed enums.)
    * Are all the files that need changes actually listed as
      components or artifacts? (Call-chain completeness at the
      artifact level.)
    * Do the ADRs reinforce the rubric's expectations, or do they
      paper over divergence?

Output is a list of ``ReviewIssue``s. MVP wiring will surface them
as plan diagnostics (same pattern as assumption review). A future
upgrade can regenerate the design section when issues are found.

Language and codebase agnostic: operates on design fields +
optional rubric + optional codebase context.
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
    "design document before the code is generated. Your job is to catch "
    "under-specification — interfaces that are vague, data models that "
    "omit specific field names the rubric or task demands, cross-"
    "component contracts that use untyped carriers where typed records "
    "belong, and missing components that the task's call chain requires. "
    "You are NOT judging prose quality — you are checking whether the "
    "design is specific enough that a competent implementer would "
    "produce the RIGHT code on the first try."
)


def _format_components(components: list[dict[str, Any]]) -> str:
    if not components:
        return "(no components recorded)"
    lines: list[str] = []
    for c in components:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "?")
        purpose = c.get("purpose", "")
        lines.append(f"### {name}")
        if purpose:
            lines.append(f"**Purpose:** {purpose}")
        responsibilities = c.get("responsibilities") or []
        if responsibilities:
            lines.append("**Responsibilities:**")
            for r in responsibilities:
                lines.append(f"  - {r}")
        interfaces = c.get("interfaces") or []
        if interfaces:
            lines.append("**Interfaces:**")
            for i in interfaces:
                lines.append(f"  - {i}")
        dependencies = c.get("dependencies") or []
        if dependencies:
            lines.append(f"**Depends on:** {', '.join(dependencies)}")
        lines.append("")
    return "\n".join(lines)


def _format_adrs(adrs: list[dict[str, Any]]) -> str:
    if not adrs:
        return "(no ADRs recorded)"
    lines: list[str] = []
    for a in adrs:
        if not isinstance(a, dict):
            continue
        title = a.get("title", "?")
        decision = a.get("decision", "")
        rationale = a.get("rationale", "")
        lines.append(f"- **{title}** — decision: {decision}")
        if rationale:
            lines.append(f"  - rationale: {rationale}")
    return "\n".join(lines)


def _format_data_model(data_model: dict[str, Any]) -> str:
    if not data_model:
        return "(no data model recorded)"
    lines: list[str] = []
    for entity, fields in data_model.items():
        if isinstance(fields, list):
            fields_str = ", ".join(str(f) for f in fields)
        else:
            fields_str = str(fields)
        lines.append(f"- **{entity}**: {fields_str}")
    return "\n".join(lines)


def _format_artifacts(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "(no artifact specs recorded)"
    lines: list[str] = []
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        filename = a.get("filename", "?")
        purpose = a.get("purpose", "")
        lines.append(f"- **{filename}**{': ' + purpose if purpose else ''}")
    return "\n".join(lines)


def _build_user_prompt(
    task_description: str,
    design: dict[str, Any],
    rubric_hints: str | None,
    gathered_context: str,
) -> str:
    context_preview = gathered_context[:4000]
    if len(gathered_context) > 4000:
        context_preview += "\n…(truncated)…"
    rubric_block = ""
    if rubric_hints and rubric_hints.strip():
        rubric_block = (
            "## Quality Criteria (domain expectations)\n\n"
            f"{rubric_hints.strip()}\n\n"
            "Check specifically: does the design name the specific fields / "
            "methods / signatures these criteria call for, or does it paper "
            "over the detail with vague language?\n\n"
        )
    return (
        f"## Task\n\n{task_description}\n\n"
        "## Design Section\n\n"
        "### Components\n"
        f"{_format_components(design.get('components') or [])}\n\n"
        "### Data Model\n"
        f"{_format_data_model(design.get('data_model') or {})}\n\n"
        "### ADRs\n"
        f"{_format_adrs(design.get('adrs') or [])}\n\n"
        "### Artifact Specs\n"
        f"{_format_artifacts(design.get('artifacts') or [])}\n\n"
        "### Integration Points\n"
        + "\n".join(f"- {ip}" for ip in (design.get("integration_points") or []))
        + "\n\n"
        f"{rubric_block}"
        "## Codebase Context (for idiom + completeness check)\n\n"
        f"{context_preview or '(none)'}\n\n"
        "## Your Task\n\n"
        "Critique the design. Look specifically for:\n\n"
        "1. **Under-specified interfaces.** A component method listed as "
        "`recordSignals(addr)` without specifying which fields are "
        "recorded; a data model entry `Address: [metadata]` without "
        "enumerating what goes in the metadata. Flag every case where "
        "the design says WHAT but not WHICH / HOW specifically.\n\n"
        "2. **Missing rubric-mandated detail.** If quality criteria "
        "demand specific field names (e.g. preserve `pre_rerank_score`), "
        "those exact names must appear in the data model or a "
        'component\'s interface list. "Preserve ranking signals" is '
        "not good enough — the implementer will settle for one "
        "composite field.\n\n"
        "3. **Leaky contracts.** Components returning raw dicts across "
        "boundaries where the task clearly benefits from typed records. "
        "Stringly-typed enums. Tuple returns that hide named fields.\n\n"
        "4. **Missing components / artifacts.** If the task's call "
        "chain has a layer (route → service → engine → synthesizer) "
        "and the design skips one, the implementer will either invent "
        "it or silently collapse two layers.\n\n"
        "5. **ADRs that don't reinforce the rubric.** An ADR titled "
        '"We record ranking signals" whose decision says "add a '
        'ranking_explanation dict" papers over the criteria demanding '
        "per-signal breakdown.\n\n"
        "Return JSON only. No prose. No code fences.\n\n"
        "{\n"
        '  "passed": true | false,\n'
        '  "issues": [\n'
        "    {\n"
        '      "target": "<component name, artifact filename, adr title, or \'data_model\'>",\n'
        '      "intent": "<what a senior engineer would expect the design to specify>",\n'
        '      "actual": "<what the design currently says, or what\'s missing>",\n'
        '      "suggestion": "<concrete addition: name the fields, rename the signature, add the missing component>"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "If the design is already specific enough to drive correct "
        'implementation, return {"passed": true, "issues": []}. Do '
        "not manufacture issues for designs that are already good — "
        "that just adds noise without improving the plan."
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
        scope="design",
        target=target,
        intent=intent,
        actual=actual,
        suggestion=suggestion,
    )


async def review_design(
    task_description: str,
    design: dict[str, Any],
    client: Any,
    *,
    rubric_hints: str | None = None,
    gathered_context: str = "",
    max_tokens: int = 4096,
    label: str = "design_review",
) -> ReviewResult:
    """Run one senior-engineer critique of the design section.

    Returns a ReviewResult with scope ``"design"``. On unparseable or
    malformed output, returns ``passed=True`` so the MVP stays purely
    additive.
    """
    if not design:
        return ReviewResult(scope="design", passed=True)

    components = design.get("components") or []
    data_model = design.get("data_model") or {}
    artifacts = design.get("artifacts") or []
    adrs = design.get("adrs") or []
    if not (components or data_model or artifacts or adrs):
        return ReviewResult(scope="design", passed=True)

    user_prompt = _build_user_prompt(
        task_description=task_description,
        design=design,
        rubric_hints=rubric_hints,
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
        logger.warning("design_review: unparseable response (%s); treating as passed", e)
        return ReviewResult(scope="design", passed=True, raw_response=raw)

    if not isinstance(parsed, dict):
        logger.warning(
            "design_review: non-object response (%s); treating as passed",
            type(parsed).__name__,
        )
        return ReviewResult(scope="design", passed=True, raw_response=raw)

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
        scope="design",
        passed=passed,
        issues=issues,
        raw_response=raw,
    )
