# tests/unit/test_synthesis_senior_assumption_review_wiring.py
"""Tests for the synthesis stage's senior assumption review pass.

After the context section is assembled (including recorded assumptions),
the stage runs ``review_assumptions`` to flag any assumptions the
codebase contradicts or that are high-impact unverifiable. Findings
are attached to the context output as ``review_findings`` so the
downstream coder sees them. MVP is purely additive — no regeneration.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.pipeline.stages import synthesis as syn_mod
from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage
from fitz_forge.planning.reviews import ReviewIssue, ReviewResult


@pytest.fixture
def stage():
    return SynthesisStage()


def _ctx(with_assumptions: bool = True) -> dict:
    return {
        "project_description": "x",
        "key_requirements": [],
        "constraints": [],
        "existing_context": "",
        "stakeholders": [],
        "scope_boundaries": {},
        "existing_files": [],
        "needed_artifacts": [],
        "assumptions": (
            [
                {
                    "assumption": "TeamCollection always has owner",
                    "impact": "sharing respects ownership",
                    "confidence": "medium",
                }
            ]
            if with_assumptions
            else []
        ),
    }


# ---------------------------------------------------------------------------
# No assumptions → short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_assumptions_skips_review(monkeypatch, stage):
    async def fake_review(**kwargs):  # pragma: no cover - must not fire
        raise AssertionError("review must not run when there are no assumptions")

    monkeypatch.setattr(syn_mod, "review_assumptions", fake_review)

    original = _ctx(with_assumptions=False)
    result = await stage._senior_assumption_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        context=original,
    )
    assert result is original


# ---------------------------------------------------------------------------
# Clean assumptions → no findings attached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_assumptions_pass_no_findings(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(scope="assumption", passed=True)

    monkeypatch.setattr(syn_mod, "review_assumptions", fake_review)

    original = _ctx()
    result = await stage._senior_assumption_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        context=original,
    )
    assert "review_findings" not in result


# ---------------------------------------------------------------------------
# Issues found → attached as review_findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issues_attached_as_review_findings(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(
            scope="assumption",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="assumption",
                    target="assumption 1",
                    intent="TeamCollection always has owner",
                    actual="team-collection.model.ts shows ownerlessTeam branch",
                    suggestion="revisit sharing assumptions",
                )
            ],
        )

    monkeypatch.setattr(syn_mod, "review_assumptions", fake_review)

    original = _ctx()
    result = await stage._senior_assumption_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        context=original,
    )
    assert "review_findings" in result
    findings = result["review_findings"]
    assert len(findings) == 1
    assert findings[0]["scope"] == "assumption"
    assert findings[0]["target"] == "assumption 1"
    assert "ownerlessTeam" in findings[0]["actual"]


# ---------------------------------------------------------------------------
# Review errors fail safely → no findings attached, original returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_exception_fails_safe(monkeypatch, stage):
    async def exploding(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(syn_mod, "review_assumptions", exploding)

    original = _ctx()
    result = await stage._senior_assumption_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        context=original,
    )
    assert result is original
    assert "review_findings" not in original


# ---------------------------------------------------------------------------
# Existing review_findings are preserved (other reviews may attach first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_findings_are_preserved(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(
            scope="assumption",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="assumption",
                    target="assumption 1",
                    intent="i",
                    actual="a",
                    suggestion="s",
                )
            ],
        )

    monkeypatch.setattr(syn_mod, "review_assumptions", fake_review)

    original = _ctx()
    original["review_findings"] = [
        {
            "scope": "other",
            "target": "t",
            "intent": "i",
            "actual": "a",
            "suggestion": "s",
        }
    ]
    result = await stage._senior_assumption_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        context=original,
    )
    findings = result["review_findings"]
    assert len(findings) == 2
    scopes = {f["scope"] for f in findings}
    assert scopes == {"other", "assumption"}


# ---------------------------------------------------------------------------
# Codebase context from prior_outputs reaches the review call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gathered_context_flows_to_review(monkeypatch, stage):
    received = {}

    async def fake_review(**kwargs):
        received.update(kwargs)
        return ReviewResult(scope="assumption", passed=True)

    monkeypatch.setattr(syn_mod, "review_assumptions", fake_review)

    await stage._senior_assumption_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={"_gathered_context": "GATHERED"},
        context=_ctx(),
    )
    assert received.get("gathered_context") == "GATHERED"
