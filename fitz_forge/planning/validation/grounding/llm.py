# fitz_forge/planning/validation/grounding/llm.py
"""LLM-based grounding: architectural gaps + targeted repair.

Path 2 (complement to the deterministic `check_artifact`):
    - build_llm_grounding_prompt: craft a prompt asking the model to spot
      missing layers, missing files, and wrong assumptions the AST can't see.
    - repair_violations: one LLM call per offending artifact to fix named
      AST violations via str.replace edits (old/new pairs).
    - validate_grounding: run both paths, return a combined GroundingReport.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fitz_forge.llm.generate import generate

from .check import Violation, check_all_artifacts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_LLM_GROUNDING_PROMPT = """You are validating a software plan's artifacts against the actual codebase.

## AST-Detected Violations
The following symbol reference errors were found deterministically:

{violations_text}

## Artifacts Being Validated
{artifacts_summary}

## Structural Index (relevant sections)
{structural_index_excerpt}

## Resolved Decisions
{decisions_summary}

## Instructions
Based on the violations above and your understanding of the codebase structure, identify:
1. **Missing layers**: Does the plan skip any intermediate layer in the call chain? (e.g., API → Service → Engine, but plan goes API → Engine directly)
2. **Missing files**: Are there files that need modification but aren't listed as artifacts?
3. **Wrong assumptions**: Do the artifacts assume helper methods exist that would need to be created first?

Be specific. Reference real file paths and method names from the structural index.
Return a JSON object:
{{
  "missing_layers": ["description of missing layer"],
  "missing_files": ["file.py — why it needs changes"],
  "wrong_assumptions": ["what the artifact assumes vs reality"],
  "summary": "1-2 sentence overall assessment"
}}"""


def _format_violations(violations: list[Violation]) -> str:
    if not violations:
        return "(none detected)"
    lines = []
    for v in violations:
        line = f"- {v.artifact}:{v.line} — {v.kind}: {v.symbol} — {v.detail}"
        if v.suggestion:
            line += f" ({v.suggestion})"
        lines.append(line)
    return "\n".join(lines)


def _format_artifacts_summary(artifacts: list[dict]) -> str:
    lines = []
    for a in artifacts:
        content = a.get("content", "")
        line_count = content.count("\n") + 1
        lines.append(f"- {a.get('filename', '?')} ({line_count} lines): {a.get('purpose', '')}")
    return "\n".join(lines)


def _format_decisions_summary(resolutions: list[dict]) -> str:
    lines = []
    for r in resolutions:
        did = r.get("decision_id", "?")
        decision = r.get("decision", "")[:200]
        lines.append(f"- {did}: {decision}")
    return "\n".join(lines)


def build_llm_grounding_prompt(
    violations: list[Violation],
    artifacts: list[dict],
    structural_index: str,
    resolutions: list[dict],
) -> str:
    """Build the LLM prompt for Path 2 gap detection."""
    artifact_files = {a.get("filename", "") for a in artifacts}
    relevant_sections: list[str] = []
    current_section: list[str] = []
    current_file = ""
    for line in structural_index.split("\n"):
        if line.startswith("## "):
            if current_section and current_file:
                for af in artifact_files:
                    if af in current_file or current_file in af:
                        relevant_sections.extend(current_section)
                        break
            current_file = line[3:].strip()
            current_section = [line]
        else:
            current_section.append(line)
    if current_section and current_file:
        for af in artifact_files:
            if af in current_file or current_file in af:
                relevant_sections.extend(current_section)
                break

    index_excerpt = "\n".join(relevant_sections) if relevant_sections else structural_index[:5000]

    return _LLM_GROUNDING_PROMPT.format(
        violations_text=_format_violations(violations),
        artifacts_summary=_format_artifacts_summary(artifacts),
        structural_index_excerpt=index_excerpt,
        decisions_summary=_format_decisions_summary(resolutions),
    )


# ---------------------------------------------------------------------------
# Targeted repair via LLM str.replace
# ---------------------------------------------------------------------------


async def repair_violations(
    violations: list[Violation],
    artifacts: list[dict[str, Any]],
    client: Any,
) -> list[dict[str, Any]]:
    """Fix AST violations in artifacts via targeted LLM repair.

    One LLM call per affected artifact. The LLM is given exact violation
    messages (machine-detected, not guesses) so it knows precisely what
    to fix. Returns the artifact list with corrections applied via str.replace.
    Artifacts with no violations are returned unchanged.
    """
    from fitz_forge.planning.pipeline.stages.base import extract_json

    violations_by_file: dict[str, list[Violation]] = {}
    for v in violations:
        violations_by_file.setdefault(v.artifact, []).append(v)

    artifact_map = {a.get("filename", ""): a for a in artifacts}
    repaired: dict[str, dict] = {}

    for filename, file_violations in violations_by_file.items():
        artifact = artifact_map.get(filename)
        if not artifact:
            continue
        content = artifact.get("content", "")
        if not content.strip():
            continue

        v_lines = []
        for v in file_violations:
            line = f"  line {v.line}: {v.symbol} — {v.detail}"
            if v.suggestion:
                line += f" ({v.suggestion})"
            v_lines.append(line)
        violations_text = "\n".join(v_lines)

        prompt = (
            f"Fix the following AST-verified errors in this code artifact.\n"
            f"These errors are machine-detected — the named methods/symbols do not "
            f"exist in the real codebase.\n\n"
            f"FILE: {filename}\n"
            f"ERRORS:\n{violations_text}\n\n"
            f"ARTIFACT CODE:\n```python\n{content}\n```\n\n"
            f"For each error, output the old text and a corrected replacement that "
            f"eliminates the fabricated symbol. Use only real existing methods.\n"
            f'Return JSON: {{"replacements": [{{"old": "exact old text", "new": "corrected text"}}]}}\n'
            f'If no correction is possible, return: {{"replacements": []}}'
        )
        messages = [
            {
                "role": "system",
                "content": "You are a code repair assistant. Fix only the specified errors.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            raw = await generate(client, messages=messages, temperature=0, max_tokens=2048)
            data = extract_json(raw)
            replacements = data.get("replacements", [])
        except Exception as e:
            logger.warning(f"Grounding repair for {filename} failed ({e}), skipping")
            continue

        if not replacements:
            logger.info(f"Grounding repair: no replacements for {filename}")
            continue

        applied = 0
        for r in replacements:
            old = r.get("old", "")
            new = r.get("new", "")
            if old and new and old != new and old in content:
                content = content.replace(old, new)
                applied += 1

        if applied:
            logger.info(
                f"Grounding repair: applied {applied}/{len(replacements)} "
                f"replacement(s) to {filename}"
            )
            repaired[filename] = {**artifact, "content": content}
        else:
            logger.info(
                f"Grounding repair: 0/{len(replacements)} replacements matched "
                f"in {filename} (old strings not found verbatim)"
            )

    if not repaired:
        return artifacts
    return [repaired.get(a.get("filename", ""), a) for a in artifacts]


# ---------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------


@dataclass
class GroundingReport:
    """Combined output from both validation paths."""

    ast_violations: list[Violation]
    llm_gaps: dict[str, Any] | None = None
    total_violations: int = 0

    def to_dict(self) -> dict:
        return {
            "ast_violations": [
                {
                    "artifact": v.artifact,
                    "line": v.line,
                    "symbol": v.symbol,
                    "kind": v.kind,
                    "detail": v.detail,
                    "suggestion": v.suggestion,
                }
                for v in self.ast_violations
            ],
            "llm_gaps": self.llm_gaps,
            "total_violations": self.total_violations,
        }


async def validate_grounding(
    artifacts: list[dict[str, Any]],
    structural_index: str,
    resolutions: list[dict[str, Any]],
    client: Any | None = None,
    source_dir: str = "",
) -> GroundingReport:
    """Run both validation paths and return combined report."""
    ast_violations = check_all_artifacts(artifacts, structural_index, source_dir=source_dir)
    logger.info(
        f"Grounding AST check: {len(ast_violations)} violations across {len(artifacts)} artifacts"
    )
    for v in ast_violations:
        logger.info(f"  {v.artifact}:{v.line} {v.kind}: {v.symbol} — {v.detail}")

    llm_gaps: dict[str, Any] | None = None
    if client is not None:
        try:
            prompt = build_llm_grounding_prompt(
                ast_violations,
                artifacts,
                structural_index,
                resolutions,
            )
            messages = [
                {
                    "role": "system",
                    "content": "You are a code reviewer validating plan artifacts against a real codebase.",
                },
                {"role": "user", "content": prompt},
            ]
            raw = await generate(client, messages=messages, max_tokens=4096)
            from fitz_forge.planning.pipeline.stages.base import extract_json

            try:
                llm_gaps = extract_json(raw)
            except ValueError:
                llm_gaps = {"raw": raw[:2000], "parse_error": True}
            logger.info(f"Grounding LLM check: {llm_gaps.get('summary', 'no summary')}")
        except Exception as e:
            logger.warning(f"Grounding LLM check failed (non-fatal): {e}")
            llm_gaps = {"error": str(e)}

    return GroundingReport(
        ast_violations=ast_violations,
        llm_gaps=llm_gaps,
        total_violations=len(ast_violations),
    )
