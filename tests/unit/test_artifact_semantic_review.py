# tests/unit/test_artifact_semantic_review.py
"""Tests for the LLM-backed semantic-review gate.

The gate reads design intent + artifact contents and returns a list of
``Discrepancy`` items. Tests stub the LLM client so the prompt shape,
output parsing, and repair-feedback formatting are exercised without a
live model.
"""

from __future__ import annotations

import json

import pytest

from fitz_forge.planning.reviews.semantic import (
    Discrepancy,
    ReviewResult,
    format_feedback,
    semantic_review,
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
    assert result.matches_intent is True
    assert result.discrepancies == []
    assert client.calls == []


# ---------------------------------------------------------------------------
# Happy path — clean pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matches_intent_no_discrepancies():
    response = json.dumps({"matches_intent": True, "discrepancies": []})
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="route -> service -> engine",
        decisions=[{"decision_id": "d1", "decision": "use streaming"}],
        artifacts=[{"filename": "a.py", "content": "def a(): pass"}],
        client=client,
    )
    assert result.matches_intent is True
    assert result.discrepancies == []
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Discrepancy path — gate reports contradictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reports_discrepancies():
    response = json.dumps(
        {
            "matches_intent": False,
            "discrepancies": [
                {
                    "file": "engine.py",
                    "line": 42,
                    "intent": "call streaming variant stream_query",
                    "actual": "calls blocking generate()",
                    "fix": "replace self._synth.generate(...) with stream_query(...)",
                },
                {
                    "file": "service.py",
                    "line": 7,
                    "intent": "yield from engine.stream_answer",
                    "actual": "calls engine.stream_answr (typo)",
                    "fix": "rename to stream_answer",
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
    assert result.matches_intent is False
    assert len(result.discrepancies) == 2
    engine_d = [d for d in result.discrepancies if d.file == "engine.py"][0]
    assert engine_d.line == 42
    assert "stream_query" in engine_d.fix


# ---------------------------------------------------------------------------
# Normalization — matches_intent/discrepancies disagreement is reconciled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matches_true_with_discrepancies_is_reconciled():
    """Model contradictorily says matches=true but lists discrepancies."""
    response = json.dumps(
        {
            "matches_intent": True,
            "discrepancies": [
                {
                    "file": "a.py",
                    "line": 1,
                    "intent": "do X",
                    "actual": "does Y",
                    "fix": "do X",
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
    assert result.matches_intent is False
    assert len(result.discrepancies) == 1


@pytest.mark.asyncio
async def test_matches_false_without_discrepancies_is_reconciled():
    response = json.dumps({"matches_intent": False, "discrepancies": []})
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.matches_intent is True
    assert result.discrepancies == []


# ---------------------------------------------------------------------------
# Robustness — malformed outputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_response_returns_matches_true():
    """Unparseable output must not spin the repair loop."""
    client = _StubClient(response="totally not JSON and has no structure")
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.matches_intent is True
    assert result.discrepancies == []


@pytest.mark.asyncio
async def test_response_is_array_treated_as_no_match():
    """Model emitted a bare array instead of an object — treat as unparseable."""
    client = _StubClient(response="[1, 2, 3]")
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.matches_intent is True
    assert result.discrepancies == []


@pytest.mark.asyncio
async def test_discrepancy_missing_required_field_dropped():
    response = json.dumps(
        {
            "matches_intent": False,
            "discrepancies": [
                {"file": "a.py", "line": 1, "intent": "x", "actual": "y", "fix": "z"},
                {"file": "b.py", "intent": "x"},  # missing actual + fix — drop
                {"line": 1, "intent": "x", "actual": "y", "fix": "z"},  # no file — drop
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
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].file == "a.py"


@pytest.mark.asyncio
async def test_non_integer_line_coerced_to_zero():
    response = json.dumps(
        {
            "matches_intent": False,
            "discrepancies": [
                {
                    "file": "a.py",
                    "line": "approximately line 7",
                    "intent": "i",
                    "actual": "a",
                    "fix": "f",
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
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].line == 0


@pytest.mark.asyncio
async def test_code_fenced_response_parses():
    """Models often wrap JSON in ```json fences — extract_json handles it."""
    response = (
        "```json\n"
        + json.dumps({"matches_intent": True, "discrepancies": []})
        + "\n```"
    )
    client = _StubClient(response=response)
    result = await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "z"}],
        client=client,
    )
    assert result.matches_intent is True


# ---------------------------------------------------------------------------
# Prompt content — decisions + artifacts land in the user message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_includes_reasoning_decisions_and_artifacts():
    client = _StubClient(response=json.dumps({"matches_intent": True, "discrepancies": []}))
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
async def test_prompt_is_sent_at_temperature_zero_with_label():
    client = _StubClient(response=json.dumps({"matches_intent": True, "discrepancies": []}))
    await semantic_review(
        reasoning="",
        decisions=[],
        artifacts=[{"filename": "a.py", "content": "b"}],
        client=client,
        label="my_label",
    )
    call = client.calls[0]
    assert call.get("max_tokens") is not None
    # temperature is passed through the generate() helper — it will appear
    # in the client kwargs when set.
    assert call.get("temperature") == 0


# ---------------------------------------------------------------------------
# Feedback formatting
# ---------------------------------------------------------------------------


def test_format_feedback_empty_is_empty_string():
    assert format_feedback([]) == ""


def test_format_feedback_renders_each_discrepancy():
    ds = [
        Discrepancy(
            file="engine.py",
            line=42,
            intent="call stream_query",
            actual="calls generate",
            fix="replace generate with stream_query",
        ),
        Discrepancy(
            file="engine.py",
            line=7,
            intent="yield incrementally",
            actual="yields full answer once",
            fix="yield chunks from stream_query",
        ),
    ]
    rendered = format_feedback(ds)
    assert "line 42" in rendered
    assert "line 7" in rendered
    assert "stream_query" in rendered
    assert "yields full answer once" in rendered


def test_review_result_defaults():
    r = ReviewResult(matches_intent=True)
    assert r.discrepancies == []
    assert r.raw_response == ""
