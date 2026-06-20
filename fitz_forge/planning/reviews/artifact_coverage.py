# fitz_forge/planning/reviews/artifact_coverage.py
"""Set-level coverage review: every needed file must ship as an artifact.

The context stage records ``needed_artifacts`` — the user-intent list of
files the plan must produce. The synthesis stage then generates
artifacts per-file. Between those two steps, entries can silently drop:
coverage-injection inflates the list past a cap, a per-file generator
fails and its output is skipped, or decomposition evidence adds files
that crowd out originals. Nothing enforces the invariant that every
``needed_artifact`` either ships as an artifact or is explicitly marked
as not-needed-after-all.

A senior engineer reading the final plan would ask: "You said we need
synthesizer.py updated. Where's synthesizer.py?" This review asks that
question mechanically.

**This review is deterministic by design.** A set-difference between
``needed_artifacts`` filenames and ``design.artifacts`` filenames is a
perfect fit for Python — no LLM call wasted on what ``set.difference()``
answers in microseconds. The review still uses the unified
``ReviewResult`` / ``ReviewIssue`` shape so the caller and the
regeneration path look identical to every other review.

Language and codebase agnostic: operates on filename strings only.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .base import ReviewIssue, ReviewResult

logger = logging.getLogger(__name__)


_ENTRY_SPLIT = re.compile(r"\s+--\s+")


def _parse_needed_entry(entry: str) -> tuple[str, str]:
    """Split a ``needed_artifacts`` entry into ``(filename, purpose)``.

    Entries come from the context stage in one of two shapes:

        "path/to/file.py -- purpose text"
        "path/to/file.py"

    The ``--`` separator is the convention used throughout the per-file
    artifact builder. Robust to extra whitespace.
    """
    if not isinstance(entry, str):
        return ("", "")
    stripped = entry.strip()
    if not stripped:
        return ("", "")
    parts = _ENTRY_SPLIT.split(stripped, maxsplit=1)
    if len(parts) == 2:
        return (parts[0].strip(), parts[1].strip())
    return (stripped, "")


def _artifact_filenames(artifacts: list[Any]) -> set[str]:
    """Extract the set of filenames actually produced.

    Accepts ``list[dict]`` (the synthesis-time in-progress shape) or
    anything else with a ``filename`` attribute. Non-dict non-attr
    entries are ignored so malformed artifacts don't crash the check.
    """
    out: set[str] = set()
    for a in artifacts or []:
        if isinstance(a, dict):
            fn = a.get("filename")
        else:
            fn = getattr(a, "filename", None)
        if isinstance(fn, str) and fn.strip():
            out.add(fn.strip())
    return out


def review_artifact_coverage(
    needed_artifacts: list[str],
    design_artifacts: list[Any],
) -> ReviewResult:
    """Check every needed file appears in the generated artifact set.

    Deterministic. Returns one ``ReviewIssue`` per missing file with
    the parsed purpose in ``suggestion`` so a regeneration pass can
    call the per-file builder with the same intent that came out of
    the context stage. When ``needed_artifacts`` is empty, the review
    passes (nothing to check against).
    """
    if not needed_artifacts:
        return ReviewResult(scope="artifact_coverage", passed=True)

    produced = _artifact_filenames(design_artifacts)
    issues: list[ReviewIssue] = []
    seen: set[str] = set()

    for entry in needed_artifacts:
        filename, purpose = _parse_needed_entry(entry)
        if not filename or filename in seen:
            continue
        seen.add(filename)
        if filename in produced:
            continue
        issues.append(
            ReviewIssue(
                scope="artifact_coverage",
                target=filename,
                intent=(
                    "Every file listed in context.needed_artifacts must "
                    "ship as an artifact — the plan commits to touching "
                    "it, so the implementer expects to find it."
                ),
                actual=(f"{filename} is in needed_artifacts but missing from design.artifacts."),
                suggestion=(
                    f"Generate the artifact for {filename}"
                    + (f" with purpose: {purpose}" if purpose else "")
                    + ". If the file is intentionally not needed, remove "
                    "it from context.needed_artifacts instead of letting "
                    "it silently drop."
                ),
            )
        )

    if issues:
        logger.info(
            "artifact_coverage: %d file(s) in needed_artifacts missing from "
            "generated artifact set: %s",
            len(issues),
            ", ".join(i.target for i in issues),
        )

    return ReviewResult(
        scope="artifact_coverage",
        passed=not issues,
        issues=issues,
    )
