# tests/unit/test_reviews_decomposition.py
"""Tests for the senior-engineer decomposition review.

The review is a narrow LLM critique of the proposed decisions: catches
disguised pattern decisions, downstream pre-commitment, missing
questions, redundancy, and call-chain gaps. This suite stubs the LLM
so prompt construction, result parsing, and normalization are
exercised without a live model.
"""

from __future__ import annotations

import json

import pytest

from fitz_forge.planning.reviews import (
    ReviewIssue,
    ReviewResult,
    format_issues_feedback,
    review_decomposition,
)


class _StubClient:
    def __init__(self, response: str, context_size: int = 32000) -> None:
        self._response = response
        self.context_size = context_size
        self.calls: list[dict] = []

    async def generate(self, **kwargs):  # noqa: D401
        self.calls.append(kwargs)
        return self._response


# ---------------------------------------------------------------------------
# Empty inputs short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_decisions_passes_without_llm_call():
    client = _StubClient(response="(should not be called)")
    result = await review_decomposition(
        task_description="x",
        decisions=[],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []
    assert client.calls == []


# ---------------------------------------------------------------------------
# Clean decomposition — passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_decomposition_passes():
    response = json.dumps({"passed": True, "issues": []})
    client = _StubClient(response=response)
    result = await review_decomposition(
        task_description="Add collection sharing",
        decisions=[
            {"id": "d1", "question": "Should sharing live in a new module?", "category": "pattern"},
        ],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Issues reported — parsed into ReviewIssue objects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issues_parsed_into_review_issues():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "target": "d1",
                    "intent": "Pattern decisions must evaluate alternatives",
                    "actual": "d1 pre-commits to extending Shortcode",
                    "suggestion": "Rephrase d1 to evaluate new module vs extension",
                },
                {
                    "target": "missing",
                    "intent": "Schema migrations need a dedicated decision",
                    "actual": "no decision covers migration path",
                    "suggestion": "Add a technical decision for migration strategy",
                },
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_decomposition(
        task_description="x",
        decisions=[{"id": "d1", "question": "q", "category": "pattern"}],
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 2
    assert result.issues[0].scope == "decomposition"
    assert result.issues[0].target == "d1"
    assert "alternatives" in result.issues[0].intent
    assert result.issues[1].target == "missing"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passed_with_issues_normalized_to_false():
    response = json.dumps(
        {
            "passed": True,
            "issues": [
                {
                    "target": "d1",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "s",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_decomposition(
        task_description="x",
        decisions=[{"id": "d1", "question": "q"}],
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 1


@pytest.mark.asyncio
async def test_failed_without_issues_normalized_to_true():
    response = json.dumps({"passed": False, "issues": []})
    client = _StubClient(response=response)
    result = await review_decomposition(
        task_description="x",
        decisions=[{"id": "d1", "question": "q"}],
        client=client,
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_response_treated_as_passed():
    client = _StubClient(response="not JSON at all, just text")
    result = await review_decomposition(
        task_description="x",
        decisions=[{"id": "d1", "question": "q"}],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []


@pytest.mark.asyncio
async def test_issue_missing_required_field_dropped():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {"target": "d1", "intent": "i", "actual": "a", "suggestion": "s"},
                {"target": "d2", "intent": "i"},  # missing actual/suggestion — drop
                {"intent": "i", "actual": "a", "suggestion": "s"},  # no target — drop
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_decomposition(
        task_description="x",
        decisions=[{"id": "d1", "question": "q"}],
        client=client,
    )
    assert len(result.issues) == 1
    assert result.issues[0].target == "d1"


# ---------------------------------------------------------------------------
# Prompt content — task + decisions + rubric flow through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_carries_task_decisions_and_rubric():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_decomposition(
        task_description="TASK-TOKEN",
        decisions=[
            {
                "id": "d1",
                "question": "QUESTION-TOKEN",
                "category": "pattern",
                "relevant_files": ["file.ts"],
            }
        ],
        client=client,
        call_graph_text="GRAPH-TOKEN",
        file_manifest="MANIFEST-TOKEN",
        rubric_hints="RUBRIC-TOKEN",
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "TASK-TOKEN" in user_prompt
    assert "d1" in user_prompt
    assert "QUESTION-TOKEN" in user_prompt
    assert "GRAPH-TOKEN" in user_prompt
    assert "MANIFEST-TOKEN" in user_prompt
    assert "RUBRIC-TOKEN" in user_prompt


@pytest.mark.asyncio
async def test_prompt_omits_rubric_section_when_absent():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_decomposition(
        task_description="x",
        decisions=[{"id": "d1", "question": "q"}],
        client=client,
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    # No rubric → the header should not appear.
    assert "Quality Criteria (domain expectations)" not in user_prompt


# ---------------------------------------------------------------------------
# format_issues_feedback — helper used by stage retries
# ---------------------------------------------------------------------------


def test_format_issues_feedback_empty_returns_empty_string():
    assert format_issues_feedback([]) == ""


def test_format_issues_feedback_renders_each_issue():
    issues = [
        ReviewIssue(
            scope="decomposition",
            target="d1",
            intent="evaluate alternatives",
            actual="pre-commits to X",
            suggestion="rephrase to evaluate X vs Y",
        ),
        ReviewIssue(
            scope="decomposition",
            target="missing",
            intent="need a migration decision",
            actual="no migration question",
            suggestion="add technical decision on migration path",
        ),
    ]
    rendered = format_issues_feedback(issues)
    assert "**d1**" in rendered
    assert "evaluate alternatives" in rendered
    assert "**missing**" in rendered
    assert "migration path" in rendered


def test_review_result_issue_count():
    r = ReviewResult(scope="decomposition", passed=True)
    assert r.issue_count == 0
    r.issues.append(
        ReviewIssue(scope="decomposition", target="d1", intent="i", actual="a", suggestion="s")
    )
    assert r.issue_count == 1
