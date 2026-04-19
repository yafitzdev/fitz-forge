# tests/unit/test_artifact_semantic_review.py
"""Tests for the LLM-backed semantic-review gate.

The gate reads design intent + artifact contents and returns a unified
``ReviewResult`` containing ``ReviewIssue``s. Tests stub the LLM client
so the prompt shape, output parsing, and repair-feedback formatting
are exercised without a live model.
"""

from __future__ import annotations

import json

import pytest

from fitz_forge.planning.reviews import (
    ReviewIssue,
    ReviewResult,
    format_issues_feedback,
    review_artifacts as semantic_review,
)


class _StubClient:
    """Minimal async LLM client that replays a scripted response."""

    def __init__(self, response: str, context_size: int = 32000) -> None:
        self._response = response
        self.context_size = context_size
        self.calls: list[dict] = []

    async def generate(self, **kwargs):  # noqa: D401
        self.calls.append(kwargs)
        return self._response


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_artifacts_short_circuits():
    client = _StubClient(response="(should not be called)")
    result = await semantic_review(
        reasoning="does not matter",
        decisions=[],
        artifacts=[],
        client=client,
    )
    assert result.scope == "artifact"
    assert result.passed is True
    assert result.issues == []
    assert client.calls == []


# ---------------------------------------------------------------------------
# Happy path — clean pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matches_intent_no_issues():
    response = json.dumps({"passed": True, "issues": []})
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="route -> service -> engine",
        decisions=[{"decision_id": "d1", "decision": "use streaming"}],
        artifacts=[{"filename": "a.py", "content": "def a(): pass"}],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Issue path — gate reports contradictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reports_issues():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "file": "engine.py",
                    "line": 42,
                    "intent": "call streaming variant stream_query",
                    "actual": "calls blocking generate()",
                    "suggestion": "replace self._synth.generate(...) with stream_query(...)",
                },
                {
                    "file": "service.py",
                    "line": 7,
                    "intent": "yield from engine.stream_answer",
                    "actual": "calls engine.stream_answr (typo)",
                    "suggestion": "rename to stream_answer",
                },
            ],
        }
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="...",
        decisions=[],
        artifacts=[
            {"filename": "engine.py", "content": "x"},
            {"filename": "service.py", "content": "y"},
        ],
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 2
    engine_issue = [i for i in result.issues if i.target.startswith("engine.py")][0]
    assert engine_issue.target == "engine.py:42"
    assert engine_issue.scope == "artifact"
    assert "stream_query" in engine_issue.suggestion


# ---------------------------------------------------------------------------
# Legacy output shape — accept matches_intent / discrepancies / fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_output_shape_still_parses():
    """Models trained on the older prompt shape should still be parsed
    cleanly — avoids a migration cliff."""
    response = json.dumps(
        {
            "matches_intent": False,
            "discrepancies": [
                {
                    "file": "engine.py",
                    "line": 42,
                    "intent": "call stream_query",
                    "actual": "calls generate",
                    "fix": "replace generate with stream_query",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "engine.py", "content": "x"}],
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 1
    assert result.issues[0].target == "engine.py:42"
    assert result.issues[0].suggestion == "replace generate with stream_query"


# ---------------------------------------------------------------------------
# Normalisation — passed vs issues disagreement resolved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passed_true_with_issues_normalises_to_false():
    response = json.dumps(
        {
            "passed": True,
            "issues": [
                {
                    "file": "a.py",
                    "line": 1,
                    "intent": "do X",
                    "actual": "does Y",
                    "suggestion": "do X",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="do X",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.passed is False
    assert len(result.issues) == 1


@pytest.mark.asyncio
async def test_passed_false_without_issues_normalises_to_true():
    response = json.dumps({"passed": False, "issues": []})
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []


# ---------------------------------------------------------------------------
# Robustness — malformed outputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_response_returns_passed_true():
    client = _StubClient(response="totally not JSON and has no structure")
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.passed is True
    assert result.issues == []


@pytest.mark.asyncio
async def test_response_is_array_treated_as_passed():
    client = _StubClient(response="[1, 2, 3]")
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.passed is True


@pytest.mark.asyncio
async def test_issue_missing_required_field_dropped():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "file": "a.py",
                    "line": 1,
                    "intent": "x",
                    "actual": "y",
                    "suggestion": "z",
                },
                {"file": "b.py", "intent": "x"},  # missing actual + suggestion
                {"line": 1, "intent": "x", "actual": "y", "suggestion": "z"},  # no file
            ],
        }
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert len(result.issues) == 1
    assert result.issues[0].target == "a.py:1"


@pytest.mark.asyncio
async def test_non_integer_line_coerced_to_zero():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "file": "a.py",
                    "line": "approximately line 7",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "f",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert len(result.issues) == 1
    # No line → target is just filename
    assert result.issues[0].target == "a.py"


@pytest.mark.asyncio
async def test_code_fenced_response_parses():
    response = (
        "```json\n"
        + json.dumps({"passed": True, "issues": []})
        + "\n```"
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_includes_reasoning_decisions_and_artifacts():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await semantic_review(
        reasoning="SYNTH-REASONING-MARKER",
        decisions=[
            {
                "decision_id": "D42",
                "decision": "DECISION-42-MARKER",
                "constraints_for_downstream": ["CONSTRAINT-MARKER"],
            }
        ],
        artifacts=[
            {"filename": "engine.py", "content": "ENGINE-CONTENT-MARKER"},
        ],
        client=client,
    )
    assert len(client.calls) == 1
    messages = client.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_text = messages[1]["content"]
    assert "SYNTH-REASONING-MARKER" in user_text
    assert "D42" in user_text
    assert "DECISION-42-MARKER" in user_text
    assert "CONSTRAINT-MARKER" in user_text
    assert "engine.py" in user_text
    assert "ENGINE-CONTENT-MARKER" in user_text


@pytest.mark.asyncio
async def test_prompt_sent_at_temperature_zero():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "b"}],
        client=client,
    )
    call = client.calls[0]
    assert call.get("max_tokens") is not None
    assert call.get("temperature") == 0


# ---------------------------------------------------------------------------
# format_issues_feedback — unified helper
# ---------------------------------------------------------------------------


def test_format_feedback_empty_is_empty_string():
    assert format_issues_feedback([]) == ""


def test_format_feedback_renders_each_issue():
    issues = [
        ReviewIssue(
            scope="artifact",
            target="engine.py:42",
            intent="call stream_query",
            actual="calls generate",
            suggestion="replace generate with stream_query",
        ),
        ReviewIssue(
            scope="artifact",
            target="engine.py:7",
            intent="yield incrementally",
            actual="yields full answer once",
            suggestion="yield chunks from stream_query",
        ),
    ]
    rendered = format_issues_feedback(issues)
    assert "engine.py:42" in rendered
    assert "engine.py:7" in rendered
    assert "stream_query" in rendered
    assert "yields full answer once" in rendered


def test_review_result_scope_is_artifact():
    r = ReviewResult(scope="artifact", passed=True)
    assert r.scope == "artifact"
    assert r.issues == []
