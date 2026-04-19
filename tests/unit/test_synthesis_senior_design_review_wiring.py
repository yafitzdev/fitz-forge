# tests/unit/test_synthesis_senior_design_review_wiring.py
"""Tests for the synthesis stage's senior design review pass.

After the design section is assembled, the stage runs review_design
to flag under-specified interfaces, rubric gaps, and missing
components. Findings attach to design.review_findings. Fully
additive MVP — no regeneration.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.pipeline.stages import synthesis as syn_mod
from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage
from fitz_forge.planning.reviews import ReviewIssue, ReviewResult


@pytest.fixture
def stage():
    return SynthesisStage()


def _design(with_content: bool = True) -> dict:
    if not with_content:
        return {
            "components": [],
            "data_model": {},
            "artifacts": [],
            "adrs": [],
            "integration_points": [],
        }
    return {
        "components": [{"name": "X", "purpose": "do things", "interfaces": []}],
        "data_model": {"Thing": ["id"]},
        "artifacts": [],
        "adrs": [],
        "integration_points": [],
    }


# ---------------------------------------------------------------------------
# Empty design / no content → skip review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_design_skips(monkeypatch, stage):
    async def fake_review(**kwargs):  # pragma: no cover - must not fire
        raise AssertionError("review must not run on empty design")

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    original = _design(with_content=False)
    result = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert result is original


# ---------------------------------------------------------------------------
# Clean design → no findings attached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_design_no_findings(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(scope="design", passed=True)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    original = _design()
    result = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert "review_findings" not in result


# ---------------------------------------------------------------------------
# Issues attached as review_findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issues_attached_as_review_findings(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(
            scope="design",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="design",
                    target="Ranker",
                    intent="Name the five signal fields on Address.metadata",
                    actual="Only 'record ranking signals' — no field names",
                    suggestion="Enumerate base_score, strategy_weight, ...",
                )
            ],
        )

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    original = _design()
    result = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert "review_findings" in result
    assert len(result["review_findings"]) == 1
    assert result["review_findings"][0]["scope"] == "design"


# ---------------------------------------------------------------------------
# Review errors fail-safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_exception_keeps_original(monkeypatch, stage):
    async def exploding(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(syn_mod, "review_design", exploding)

    original = _design()
    result = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert result is original


# ---------------------------------------------------------------------------
# Existing review_findings preserved (other reviews land first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_findings_preserved(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(
            scope="design",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="design",
                    target="X",
                    intent="i",
                    actual="a",
                    suggestion="s",
                )
            ],
        )

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    original = _design()
    original["review_findings"] = [
        {
            "scope": "other",
            "target": "t",
            "intent": "i",
            "actual": "a",
            "suggestion": "s",
        }
    ]
    result = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert len(result["review_findings"]) == 2
    scopes = {f["scope"] for f in result["review_findings"]}
    assert scopes == {"other", "design"}


# ---------------------------------------------------------------------------
# Rubric + gathered context propagate to the review call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rubric_and_context_reach_review(monkeypatch, stage):
    received = {}

    async def fake_review(**kwargs):
        received.update(kwargs)
        return ReviewResult(scope="design", passed=True)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={
            "_rubric_hints": "RUBRIC",
            "_gathered_context": "GATHERED",
        },
        design=_design(),
    )
    assert received.get("rubric_hints") == "RUBRIC"
    assert received.get("gathered_context") == "GATHERED"
