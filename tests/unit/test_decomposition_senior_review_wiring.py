# tests/unit/test_decomposition_senior_review_wiring.py
"""Integration tests for the decomposition stage's senior-review pass.

After the existing deterministic gates (count, specificity, deps,
ref_complete, coverage) have selected a candidate, the stage runs one
LLM critique via review_decomposition. When issues are found it
regenerates once with feedback and keeps whichever result has fewer
issues. These tests monkeypatch review_decomposition + generate so the
control flow is exercised without a live model.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.pipeline.stages import decision_decomposition as dd
from fitz_forge.planning.pipeline.stages.decision_decomposition import (
    DecisionDecompositionStage,
)
from fitz_forge.planning.reviews import ReviewIssue, ReviewResult


@pytest.fixture
def stage():
    return DecisionDecompositionStage()


def _make_parsed(ids=("d1", "d2")):
    return {
        "decisions": [
            {
                "id": did,
                "question": f"Question {did}",
                "category": "pattern",
                "depends_on": [],
                "relevant_files": ["x.py"],
            }
            for did in ids
        ]
    }


# ---------------------------------------------------------------------------
# Review passes → original decomposition returned unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_passed_keeps_original(monkeypatch, stage):
    parsed = _make_parsed()
    review_calls = []

    async def fake_review(*, decisions, **kwargs):
        review_calls.append(decisions)
        return ReviewResult(scope="decomposition", passed=True)

    monkeypatch.setattr(dd, "review_decomposition", fake_review)

    async def _no_generate(*a, **k):  # pragma: no cover - must not fire
        raise AssertionError("generate must not run when review passes")

    monkeypatch.setattr(dd, "generate", _no_generate)

    result = await stage._senior_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={"_call_graph_text": "g", "_raw_summaries": "m"},
        parsed=parsed,
        decisions=parsed["decisions"],
        raw="raw",
    )
    out_parsed, out_decisions, out_raw = result
    assert out_parsed is parsed
    assert out_decisions == parsed["decisions"]
    assert out_raw == "raw"
    assert len(review_calls) == 1


# ---------------------------------------------------------------------------
# Review finds issues → regenerate → retry has fewer issues → use retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_issues_trigger_regen_and_retry_improves(monkeypatch, stage):
    original = _make_parsed()
    retry_parsed = _make_parsed(("d1", "d2", "d3"))

    review_states = iter(
        [
            ReviewResult(
                scope="decomposition",
                passed=False,
                issues=[
                    ReviewIssue(
                        scope="decomposition",
                        target="d1",
                        intent="evaluate alternatives",
                        actual="pre-commits",
                        suggestion="rephrase",
                    )
                ],
            ),
            ReviewResult(scope="decomposition", passed=True),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    monkeypatch.setattr(dd, "review_decomposition", fake_review)

    async def fake_generate(*args, **kwargs):
        assert "senior" in kwargs.get("label", "")
        return "retry raw text"

    monkeypatch.setattr(dd, "generate", fake_generate)
    monkeypatch.setattr(
        stage,
        "parse_output",
        lambda raw: retry_parsed,
    )

    out_parsed, out_decisions, out_raw = await stage._senior_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={"_call_graph_text": "g", "_raw_summaries": "m"},
        parsed=original,
        decisions=original["decisions"],
        raw="original raw",
    )
    # retry had 0 issues, original had 1 → retry wins
    assert out_parsed is retry_parsed
    assert out_raw == "retry raw text"
    assert len(out_decisions) == 3


# ---------------------------------------------------------------------------
# Retry doesn't improve → original returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_does_not_improve_returns_original(monkeypatch, stage):
    original = _make_parsed()
    retry_parsed = _make_parsed()

    issues_list = [
        ReviewIssue(
            scope="decomposition", target="d1", intent="i", actual="a", suggestion="s"
        )
    ]
    # Original: 1 issue. Retry: 2 issues (worse). Expect original returned.
    review_states = iter(
        [
            ReviewResult(scope="decomposition", passed=False, issues=list(issues_list)),
            ReviewResult(
                scope="decomposition",
                passed=False,
                issues=issues_list
                + [
                    ReviewIssue(
                        scope="decomposition",
                        target="d2",
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

    async def fake_generate(*args, **kwargs):
        return "retry raw"

    monkeypatch.setattr(dd, "review_decomposition", fake_review)
    monkeypatch.setattr(dd, "generate", fake_generate)
    monkeypatch.setattr(stage, "parse_output", lambda raw: retry_parsed)

    out_parsed, out_decisions, out_raw = await stage._senior_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={"_call_graph_text": "g", "_raw_summaries": "m"},
        parsed=original,
        decisions=original["decisions"],
        raw="original raw",
    )
    assert out_parsed is original
    assert out_raw == "original raw"


# ---------------------------------------------------------------------------
# Review errors → fail-safe: keep original
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_exception_keeps_original(monkeypatch, stage):
    original = _make_parsed()

    async def exploding_review(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dd, "review_decomposition", exploding_review)

    out_parsed, out_decisions, out_raw = await stage._senior_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={"_call_graph_text": "", "_raw_summaries": ""},
        parsed=original,
        decisions=original["decisions"],
        raw="original raw",
    )
    assert out_parsed is original


# ---------------------------------------------------------------------------
# Rubric hints from prior_outputs propagate into review call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rubric_hints_reach_review(monkeypatch, stage):
    original = _make_parsed()
    received = {}

    async def fake_review(**kwargs):
        received.update(kwargs)
        return ReviewResult(scope="decomposition", passed=True)

    monkeypatch.setattr(dd, "review_decomposition", fake_review)

    await stage._senior_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={
            "_call_graph_text": "graph",
            "_raw_summaries": "manifest",
            "_rubric_hints": "RUBRIC",
        },
        parsed=original,
        decisions=original["decisions"],
        raw="raw",
    )
    assert received.get("rubric_hints") == "RUBRIC"
    assert received.get("call_graph_text") == "graph"
    assert received.get("file_manifest") == "manifest"
