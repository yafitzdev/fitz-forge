# tests/unit/test_synthesis_senior_arch_review_wiring.py
"""Tests for the synthesis stage's senior architecture review pass.

After architecture per-field extraction, the stage runs
``review_architecture`` on the typed output. When issues are flagged
it re-extracts with the feedback appended to the reasoning and keeps
whichever pass the re-review prefers. These tests monkeypatch the
review + extraction entry points so the control flow is exercised
without a live model.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.pipeline.stages import synthesis as syn_mod
from fitz_forge.planning.pipeline.stages.synthesis import (
    SynthesisStage,
)
from fitz_forge.planning.reviews import ReviewIssue, ReviewResult


@pytest.fixture
def stage():
    return SynthesisStage()


def _arch(recommended: str = "Approach X") -> dict:
    return {
        "recommended": recommended,
        "reasoning": "some reasoning",
        "approaches": [
            {
                "name": recommended,
                "description": "...",
                "pros": [],
                "cons": [],
                "recommended": True,
            }
        ],
        "key_tradeoffs": {},
        "technology_considerations": [],
        "scope_statement": "",
    }


# ---------------------------------------------------------------------------
# Review passes -> original architecture unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_pass_keeps_original(monkeypatch, stage):
    arch = _arch("Dedicated CollectionShare module")
    review_calls = []

    async def fake_review(*, architecture, **kwargs):
        review_calls.append(architecture)
        return ReviewResult(scope="architecture", passed=True)

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)

    async def fake_extract(*args, **kwargs):  # pragma: no cover - must not fire
        raise AssertionError("re-extraction must not run when review passes")

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=arch,
        reasoning="original reasoning",
        extract_context="ctx",
    )
    assert result is arch
    assert len(review_calls) == 1


# ---------------------------------------------------------------------------
# Review finds issues -> re-extract -> retry improves -> use retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_issues_trigger_reextract_and_retry_wins(monkeypatch, stage):
    original = _arch("Blocking + split")
    review_states = iter(
        [
            ReviewResult(
                scope="architecture",
                passed=False,
                issues=[
                    ReviewIssue(
                        scope="architecture",
                        target="architecture.recommended",
                        intent="Real streaming",
                        actual="buffers + splits",
                        suggestion="Switch to generator pattern",
                    )
                ],
            ),
            ReviewResult(scope="architecture", passed=True),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    # Two extract calls per run (one per _ARCH_FIELD_GROUPS entry in real code).
    # The fake returns the fields the retry wants.
    extract_responses = {
        "recommended": "Generator streaming pattern",
        "reasoning": "yield from synthesizer.stream_generate",
        "approaches": [
            {
                "name": "Generator streaming pattern",
                "description": "...",
                "pros": [],
                "cons": [],
                "recommended": True,
            }
        ],
        "key_tradeoffs": {},
        "technology_considerations": [],
        "scope_statement": "",
    }

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        assert "after_review" in label
        # Return only the requested fields.
        return {f: extract_responses[f] for f in fields if f in extract_responses}

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning="reasoning X",
        extract_context="ctx",
    )
    # retry had 0 issues, original had 1 → retry wins.
    assert result["recommended"] == "Generator streaming pattern"


# ---------------------------------------------------------------------------
# Retry doesn't improve -> fall back to original
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_no_improvement_keeps_original(monkeypatch, stage):
    original = _arch("Approach Y")
    issues = [
        ReviewIssue(
            scope="architecture",
            target="architecture.recommended",
            intent="i",
            actual="a",
            suggestion="s",
        )
    ]
    review_states = iter(
        [
            ReviewResult(scope="architecture", passed=False, issues=list(issues)),
            ReviewResult(
                scope="architecture",
                passed=False,
                issues=issues
                + [
                    ReviewIssue(
                        scope="architecture",
                        target="architecture.approaches",
                        intent="i",
                        actual="a",
                        suggestion="s",
                    )
                ],
            ),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        return {
            "recommended": "Worse Pick",
            "reasoning": "x",
            "approaches": [
                {
                    "name": "Worse Pick",
                    "description": "...",
                    "pros": [],
                    "cons": [],
                    "recommended": True,
                }
            ],
            "key_tradeoffs": {},
            "technology_considerations": [],
            "scope_statement": "",
        }

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning="x",
        extract_context="ctx",
    )
    assert result["recommended"] == "Approach Y"


# ---------------------------------------------------------------------------
# Review errors / empty re-extraction -> fail-safe: keep original
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_exception_keeps_original(monkeypatch, stage):
    original = _arch("Approach Z")

    async def exploding(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(syn_mod, "review_architecture", exploding)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning="x",
        extract_context="ctx",
    )
    assert result is original


@pytest.mark.asyncio
async def test_empty_reextraction_keeps_original(monkeypatch, stage):
    original = _arch("Approach Q")

    async def fake_review(**kwargs):
        return ReviewResult(
            scope="architecture",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="architecture",
                    target="architecture.recommended",
                    intent="i",
                    actual="a",
                    suggestion="s",
                )
            ],
        )

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        return {f: "" for f in fields}

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning="x",
        extract_context="ctx",
    )
    assert result["recommended"] == "Approach Q"


# ---------------------------------------------------------------------------
# rubric_hints from prior_outputs flows into the review call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rubric_hints_reach_review(monkeypatch, stage):
    received = {}

    async def fake_review(**kwargs):
        received.update(kwargs)
        return ReviewResult(scope="architecture", passed=True)

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)

    await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={"_rubric_hints": "RUBRIC", "_gathered_context": "GATHERED"},
        architecture=_arch(),
        reasoning="x",
        extract_context="ctx",
    )
    assert received.get("rubric_hints") == "RUBRIC"
    assert received.get("gathered_context") == "GATHERED"
