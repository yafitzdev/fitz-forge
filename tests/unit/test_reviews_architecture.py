# tests/unit/test_reviews_architecture.py
"""Tests for the senior-engineer architecture review.

The review fires after synthesis extracts the architecture section
and critiques the chosen recommendation. Catches cases where the
model picks a plausible-sounding but structurally wrong approach
(e.g. fake streaming, caching without invalidation) that coherent
downstream stages would never reveal on their own.
"""

from __future__ import annotations

import json

import pytest

from fitz_forge.planning.reviews import ReviewResult, review_architecture


class _StubClient:
    def __init__(self, response: str, context_size: int = 32000) -> None:
        self._response = response
        self.context_size = context_size
        self.calls: list[dict] = []

    async def generate(self, **kwargs):  # noqa: D401
        self.calls.append(kwargs)
        return self._response


# ---------------------------------------------------------------------------
# Empty / guard-rail short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_architecture_short_circuits():
    client = _StubClient(response="(unused)")
    result = await review_architecture(
        task_description="x",
        architecture={},
        client=client,
    )
    assert result.passed is True
    assert client.calls == []


@pytest.mark.asyncio
async def test_missing_recommended_skips_review():
    client = _StubClient(response="(unused)")
    result = await review_architecture(
        task_description="x",
        architecture={"reasoning": "...", "approaches": []},
        client=client,
    )
    assert result.passed is True
    assert client.calls == []


# ---------------------------------------------------------------------------
# Happy path: clean recommendation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_recommendation_passes():
    response = json.dumps({"passed": True, "issues": []})
    client = _StubClient(response=response)
    result = await review_architecture(
        task_description="add collection sharing",
        architecture={
            "recommended": "Dedicated CollectionShare module",
            "reasoning": "Cleanest separation",
            "approaches": [{"name": "extend Shortcode", "description": "..."}],
        },
        client=client,
    )
    assert result.passed is True
    assert result.issues == []
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Issues path: bad recommendation flagged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_streaming_recommendation_flagged():
    """Classic V7 A4 failure shape: 'add streaming' via buffer + split."""
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "target": "architecture.recommended",
                    "intent": "Real streaming — yield incrementally from the provider",
                    "actual": "Buffers the full answer then splits into fake tokens",
                    "suggestion": (
                        "Switch to a generator-based pipeline that yields "
                        "chunks from the synthesizer's stream_generate call"
                    ),
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_architecture(
        task_description="add token streaming",
        architecture={
            "recommended": "Blocking + split",
            "reasoning": "Produce answer, split its text into tokens, yield each",
            "approaches": [
                {"name": "Blocking + split", "description": "..."},
                {"name": "Provider streaming", "description": "..."},
            ],
        },
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 1
    assert result.issues[0].scope == "architecture"
    assert result.issues[0].target == "architecture.recommended"


# ---------------------------------------------------------------------------
# Normalization (mirrors other reviews)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passed_with_issues_normalizes_to_false():
    response = json.dumps(
        {
            "passed": True,
            "issues": [
                {
                    "target": "architecture.recommended",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "s",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_architecture(
        task_description="x",
        architecture={"recommended": "something", "reasoning": "x"},
        client=client,
    )
    assert result.passed is False


@pytest.mark.asyncio
async def test_failed_without_issues_normalizes_to_true():
    response = json.dumps({"passed": False, "issues": []})
    client = _StubClient(response=response)
    result = await review_architecture(
        task_description="x",
        architecture={"recommended": "something"},
        client=client,
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_response_treated_as_passed():
    client = _StubClient(response="not JSON at all")
    result = await review_architecture(
        task_description="x",
        architecture={"recommended": "something"},
        client=client,
    )
    assert result.passed is True


@pytest.mark.asyncio
async def test_bad_issue_shape_dropped():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "target": "architecture.recommended",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "s",
                },
                {"target": "architecture.recommended"},  # missing fields
                {},
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_architecture(
        task_description="x",
        architecture={"recommended": "something"},
        client=client,
    )
    assert len(result.issues) == 1


# ---------------------------------------------------------------------------
# Prompt content — task + architecture + rubric + codebase context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_carries_all_context():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_architecture(
        task_description="TASK-TOKEN",
        architecture={
            "recommended": "REC-TOKEN",
            "reasoning": "REASONING-TOKEN",
            "approaches": [
                {"name": "APPROACH-TOKEN", "description": "..."}
            ],
            "key_tradeoffs": "TRADEOFFS-TOKEN",
        },
        client=client,
        gathered_context="CODEBASE-TOKEN",
        rubric_hints="RUBRIC-TOKEN",
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "TASK-TOKEN" in user_prompt
    assert "REC-TOKEN" in user_prompt
    assert "REASONING-TOKEN" in user_prompt
    assert "APPROACH-TOKEN" in user_prompt
    assert "TRADEOFFS-TOKEN" in user_prompt
    assert "CODEBASE-TOKEN" in user_prompt
    assert "RUBRIC-TOKEN" in user_prompt


@pytest.mark.asyncio
async def test_prompt_omits_rubric_header_when_absent():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_architecture(
        task_description="x",
        architecture={"recommended": "rec"},
        client=client,
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "Quality Criteria (domain expectations)" not in user_prompt


@pytest.mark.asyncio
async def test_codebase_context_truncated_when_large():
    """Keep the review fast; cap the context it reads."""
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    big_context = "x" * 8000
    await review_architecture(
        task_description="x",
        architecture={"recommended": "rec"},
        client=client,
        gathered_context=big_context,
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "…(truncated)…" in user_prompt


def test_review_result_scope_is_architecture():
    r = ReviewResult(scope="architecture", passed=True)
    assert r.scope == "architecture"
