# tests/unit/test_reviews_design.py
"""Tests for the senior-engineer design review.

The review fires after the design section assembles and critiques
interface specificity, data-model precision, rubric adherence,
cross-component contracts, and call-chain completeness.
"""

from __future__ import annotations

import json

import pytest

from fitz_forge.planning.reviews import ReviewResult, review_design


class _StubClient:
    def __init__(self, response: str, context_size: int = 32000) -> None:
        self._response = response
        self.context_size = context_size
        self.calls: list[dict] = []

    async def generate(self, **kwargs):  # noqa: D401
        self.calls.append(kwargs)
        return self._response


def _design(
    components=None,
    data_model=None,
    artifacts=None,
    adrs=None,
    integration_points=None,
) -> dict:
    return {
        "components": components or [],
        "data_model": data_model or {},
        "artifacts": artifacts or [],
        "adrs": adrs or [],
        "integration_points": integration_points or [],
    }


# ---------------------------------------------------------------------------
# Empty-design guard rails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_design_short_circuits():
    client = _StubClient(response="(unused)")
    result = await review_design(
        task_description="x",
        design={},
        client=client,
    )
    assert result.passed is True
    assert client.calls == []


@pytest.mark.asyncio
async def test_all_empty_sections_short_circuits():
    client = _StubClient(response="(unused)")
    result = await review_design(
        task_description="x",
        design=_design(),
        client=client,
    )
    assert result.passed is True
    assert client.calls == []


# ---------------------------------------------------------------------------
# Clean design passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_design_passes():
    response = json.dumps({"passed": True, "issues": []})
    client = _StubClient(response=response)
    result = await review_design(
        task_description="x",
        design=_design(
            components=[
                {
                    "name": "Ranker",
                    "purpose": "score candidates",
                    "interfaces": [
                        "_compute_score records base_score, strategy_weight, "
                        "entity_bonus, keyword_boost, composite_score on Address.metadata"
                    ],
                }
            ],
            data_model={
                "Address.metadata": [
                    "base_score",
                    "strategy_weight",
                    "entity_bonus",
                    "keyword_boost",
                    "composite_score",
                ]
            },
        ),
        client=client,
    )
    assert result.passed is True
    assert result.issues == []


# ---------------------------------------------------------------------------
# Under-specified interface flagged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_under_specified_interface_flagged():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {
                    "target": "Ranker",
                    "intent": (
                        "Record the five individual signals "
                        "(base_score, strategy_weight, entity_bonus, "
                        "keyword_boost, composite_score) on "
                        "Address.metadata"
                    ),
                    "actual": (
                        "Interface says 'record ranking signals' with no "
                        "specific fields — will produce a single composite "
                        "dict"
                    ),
                    "suggestion": (
                        "List the five field names explicitly on the "
                        "Ranker's interface line and in the data model"
                    ),
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_design(
        task_description="ranking explanations",
        design=_design(
            components=[
                {
                    "name": "Ranker",
                    "purpose": "rank",
                    "interfaces": ["record ranking signals on metadata"],
                }
            ],
        ),
        client=client,
        rubric_hints=(
            "Record base_score, strategy_weight, entity_bonus, "
            "keyword_boost, composite_score separately."
        ),
    )
    assert result.passed is False
    assert len(result.issues) == 1
    assert result.issues[0].scope == "design"
    assert result.issues[0].target == "Ranker"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passed_with_issues_normalizes_to_false():
    response = json.dumps(
        {
            "passed": True,
            "issues": [
                {
                    "target": "X",
                    "intent": "i",
                    "actual": "a",
                    "suggestion": "s",
                }
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_design(
        task_description="x",
        design=_design(components=[{"name": "X", "purpose": "y"}]),
        client=client,
    )
    assert result.passed is False


@pytest.mark.asyncio
async def test_failed_without_issues_normalizes_to_true():
    response = json.dumps({"passed": False, "issues": []})
    client = _StubClient(response=response)
    result = await review_design(
        task_description="x",
        design=_design(components=[{"name": "X", "purpose": "y"}]),
        client=client,
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_response_treated_as_passed():
    client = _StubClient(response="not JSON")
    result = await review_design(
        task_description="x",
        design=_design(components=[{"name": "X", "purpose": "y"}]),
        client=client,
    )
    assert result.passed is True


@pytest.mark.asyncio
async def test_bad_issue_shape_dropped():
    response = json.dumps(
        {
            "passed": False,
            "issues": [
                {"target": "X", "intent": "i", "actual": "a", "suggestion": "s"},
                {"target": "Y"},  # incomplete
                {},
            ],
        }
    )
    client = _StubClient(response=response)
    result = await review_design(
        task_description="x",
        design=_design(components=[{"name": "X", "purpose": "y"}]),
        client=client,
    )
    assert len(result.issues) == 1


# ---------------------------------------------------------------------------
# Prompt content — all design sections + rubric + codebase flow through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_carries_all_design_sections():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_design(
        task_description="TASK-TOKEN",
        design=_design(
            components=[
                {
                    "name": "COMPONENT-TOKEN",
                    "purpose": "PURPOSE-TOKEN",
                    "interfaces": ["IFACE-TOKEN"],
                }
            ],
            data_model={"ENTITY-TOKEN": ["FIELD-TOKEN"]},
            adrs=[
                {
                    "title": "ADR-TOKEN",
                    "decision": "DECISION-TOKEN",
                    "rationale": "RATIONALE-TOKEN",
                    "context": "c",
                }
            ],
            artifacts=[{"filename": "ARTIFACT-TOKEN", "content": "c", "purpose": "ART-PURPOSE"}],
            integration_points=["INTEGRATION-TOKEN"],
        ),
        client=client,
        rubric_hints="RUBRIC-TOKEN",
        gathered_context="CODEBASE-TOKEN",
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    for token in (
        "TASK-TOKEN",
        "COMPONENT-TOKEN",
        "PURPOSE-TOKEN",
        "IFACE-TOKEN",
        "ENTITY-TOKEN",
        "FIELD-TOKEN",
        "ADR-TOKEN",
        "DECISION-TOKEN",
        "RATIONALE-TOKEN",
        "ARTIFACT-TOKEN",
        "INTEGRATION-TOKEN",
        "RUBRIC-TOKEN",
        "CODEBASE-TOKEN",
    ):
        assert token in user_prompt, f"missing {token}"


@pytest.mark.asyncio
async def test_prompt_omits_rubric_header_when_absent():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    await review_design(
        task_description="x",
        design=_design(components=[{"name": "X", "purpose": "y"}]),
        client=client,
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "Quality Criteria (domain expectations)" not in user_prompt


@pytest.mark.asyncio
async def test_prompt_truncates_large_codebase_context():
    client = _StubClient(response=json.dumps({"passed": True, "issues": []}))
    big = "x" * 8000
    await review_design(
        task_description="x",
        design=_design(components=[{"name": "X", "purpose": "y"}]),
        client=client,
        gathered_context=big,
    )
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "…(truncated)…" in user_prompt


def test_review_result_scope_is_design():
    r = ReviewResult(scope="design", passed=True)
    assert r.scope == "design"
