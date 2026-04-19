# tests/unit/test_reviews_assumptions.py
"""Tests for the senior-engineer adversarial assumption review.

The review reads the junior's recorded assumptions against the
codebase context and flags those demonstrably contradicted or
high-impact unverifiable.
"""

from __future__ import annotations

import json

import pytest

from fitz_forge.planning.reviews import ReviewResult, review_assumptions


class _StubClient:
    def __init__(self, response: str, context_size: int = 32000) -> None:
        self._response = response
        self.context_size = context_size
        self.calls: list[dict] = []

    async def generate(self, **kwargs):  # noqa: D401
        self.calls.append(kwargs)
        return self._response


# ---------------------------------------------------------------------------
# Guard-rails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_assumptions_short_circuits():
    client = _StubClient(response="(unused)")
    result = await review_assumptions(
        task_description="x",
        assumptions=[],
        client=client,
    )
    assert result.passed is True
    assert client.calls == []


# ---------------------------------------------------------------------------
# Clean assumptions pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supported_assumptions_pass():
    response = json.dumps({"passed": True, "issues": []})
    client = _StubClient(response=response)
    result = await review_assumptions(
        task_description="x",
        assumptions=[
            {"assumption": "team owners always exist", "impact": "low", "confidence": "high"}
        ],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []


# ---------------------------------------------------------------------------
# Contradicted assumption reported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contradicted_assumption_flagged():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "target": "assumption 1",
                    "intent": "TeamCollection always has a team owner",
                    "actual": (
                        "team-collection.model.ts defines an `ownerlessTeam` branch "
                        "where TeamCollection can exist without an owner"
                    ),
                    "suggestion": (
                        "Revisit any decision that assumes teamOwner is non-null; "
                        "add a migration for ownerless branches"
                    ),
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_assumptions(
        task_description="add collection sharing",
        assumptions=[
            {
                "assumption": "TeamCollection always has a team owner",
                "impact": "sharing respects owner team",
                "confidence": "medium",
            }
        ],
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 1
    assert result.issues[0].scope == "assumption"
    assert "ownerlessTeam" in result.issues[0].actual


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passed_with_issues_normalized_to_false():
    response = json.dumps(
        {
            "passed": True,
            "issues": [
                {
                    "target": "assumption 1",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "s",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_assumptions(
        task_description="x",
        assumptions=[{"assumption": "y"}],
        client=client,
    )
    assert result.passed is False


@pytest.mark.asyncio
async def test_failed_without_issues_normalized_to_true():
    response = json.dumps({"passed": False, "issues": []})
    client = _StubClient(response=response)
    result = await review_assumptions(
        task_description="x",
        assumptions=[{"assumption": "y"}],
        client=client,
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_response_treated_as_passed():
    client = _StubClient(response="not JSON")
    result = await review_assumptions(
        task_description="x",
        assumptions=[{"assumption": "y"}],
        client=client,
    )
    assert result.passed is True


@pytest.mark.asyncio
async def test_bad_issue_shapes_dropped():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "target": "assumption 1",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "s",
                },
                {"target": "assumption 2"},  # incomplete
                {},  # empty
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_assumptions(
        task_description="x",
        assumptions=[{"assumption": "y"}],
        client=client,
    )
    assert len(result.issues) == 1


# ---------------------------------------------------------------------------
# Prompt content — assumptions + task + codebase flow through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_carries_assumptions_and_context():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_assumptions(
        task_description="TASK-TOKEN",
        assumptions=[
            {
                "assumption": "ASSUMPTION-TOKEN",
                "impact": "IMPACT-TOKEN",
                "confidence": "CONFIDENCE-TOKEN",
            }
        ],
        client=client,
        gathered_context="CODEBASE-TOKEN",
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "TASK-TOKEN" in user_prompt
    assert "ASSUMPTION-TOKEN" in user_prompt
    assert "IMPACT-TOKEN" in user_prompt
    assert "CONFIDENCE-TOKEN" in user_prompt
    assert "CODEBASE-TOKEN" in user_prompt


@pytest.mark.asyncio
async def test_prompt_truncates_large_context():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    big_context = "x" * 10000
    await review_assumptions(
        task_description="x",
        assumptions=[{"assumption": "y"}],
        client=client,
        gathered_context=big_context,
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "…(truncated)…" in user_prompt


def test_review_result_scope_is_assumption():
    r = ReviewResult(scope="assumption", passed=True)
    assert r.scope == "assumption"
