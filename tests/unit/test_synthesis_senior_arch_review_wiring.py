# tests/unit/test_synthesis_senior_arch_review_wiring.py
"""Tests for the synthesis stage's senior architecture review pass.

After architecture per-field extraction, the stage runs
``review_architecture`` on the typed output. When issues are flagged
it **regenerates the synthesis reasoning** with the critique merged
into the prompt and re-extracts from the fresh reasoning. Simple
re-extraction from the original reasoning can't flip the pick
because the text still argues for the wrong approach. The sanity
gate rejects dramatically worse reasoning (below 70% of the
original's ``_score_reasoning`` score) and the final retry-review
gate keeps whichever pass has fewer issues.
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


def _stub_regen_prompt(monkeypatch, stage) -> None:
    """Replace build_prompt with a minimal stub so the regen call is
    independent of the real synthesis prompt template.
    """
    def fake_build_prompt(self, job_description, prior_outputs):
        return [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
    monkeypatch.setattr(
        SynthesisStage, "build_prompt", fake_build_prompt
    )


def _stub_generate(monkeypatch, new_reasoning: str) -> list[str]:
    """Replace module-level ``generate`` with one that returns
    ``new_reasoning`` for the reasoning-regen call. Captures labels so
    tests can assert which call fired.
    """
    captured_labels: list[str] = []

    async def fake_generate(client, messages, **kwargs):
        captured_labels.append(kwargs.get("label", ""))
        return new_reasoning

    monkeypatch.setattr(syn_mod, "generate", fake_generate)
    return captured_labels


# A reasoning string dense enough to clear the 70% scope gate.
_RICH_REASONING = (
    "The plan modifies fitz_sage/engines/fitz_krag/engine.py and "
    "fitz_sage/engines/fitz_krag/retrieval/ranker.py and "
    "fitz_sage/engines/fitz_krag/retrieval/reranker.py and "
    "fitz_sage/api/routes/query.py and "
    "fitz_sage/engines/fitz_krag/generation/synthesizer.py. "
    "Architecture: single-pass approach preserves the existing "
    "call_pattern, component interfaces, and milestone phase 1 "
    "through the mitigation risk gate. "
    "CrossStrategyRanker, AddressReranker, CodeSynthesizer, "
    "FitzKragEngine, and the data_model are the central classes. "
    "rank_signals, record_breakdown, pre_rerank_score, retrieval_method "
    "and composite_score are the named fields. "
    "Decision milestone deliverable and mitigation risk keep the "
    "plan within scope. "
    + ("Filler text. " * 400)
)

_JUNK_REASONING = "ok"


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
# Review finds issues -> reasoning regen -> retry wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_issues_trigger_reasoning_regen_and_retry_wins(
    monkeypatch, stage
):
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

    _stub_regen_prompt(monkeypatch, stage)
    labels = _stub_generate(monkeypatch, new_reasoning=_RICH_REASONING)

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
        return {f: extract_responses[f] for f in fields if f in extract_responses}

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning=_RICH_REASONING,
        extract_context="ctx",
    )
    assert result["recommended"] == "Generator streaming pattern"
    assert "synthesis_reasoning_after_arch_review" in labels


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

    _stub_regen_prompt(monkeypatch, stage)
    _stub_generate(monkeypatch, new_reasoning=_RICH_REASONING)

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
        reasoning=_RICH_REASONING,
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

    _stub_regen_prompt(monkeypatch, stage)
    _stub_generate(monkeypatch, new_reasoning=_RICH_REASONING)

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        return {f: "" for f in fields}

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning=_RICH_REASONING,
        extract_context="ctx",
    )
    assert result["recommended"] == "Approach Q"


# ---------------------------------------------------------------------------
# Reasoning-regen produces junk -> scope gate rejects, keep original
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_regen_below_score_gate_keeps_original(
    monkeypatch, stage
):
    original = _arch("Good Approach")

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

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    _stub_regen_prompt(monkeypatch, stage)
    _stub_generate(monkeypatch, new_reasoning=_JUNK_REASONING)

    async def fake_extract(*args, **kwargs):  # pragma: no cover
        raise AssertionError("re-extraction must not run when scope gate rejects")

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning=_RICH_REASONING,
        extract_context="ctx",
    )
    assert result is original


# ---------------------------------------------------------------------------
# Reasoning-regen generate() raises -> keep original
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_regen_generate_failure_keeps_original(
    monkeypatch, stage
):
    original = _arch("Safe Approach")

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

    async def exploding_generate(client, messages, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(syn_mod, "review_architecture", fake_review)
    _stub_regen_prompt(monkeypatch, stage)
    monkeypatch.setattr(syn_mod, "generate", exploding_generate)

    async def fake_extract(*args, **kwargs):  # pragma: no cover
        raise AssertionError("re-extraction must not run when regen failed")

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    result = await stage._senior_arch_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        architecture=original,
        reasoning=_RICH_REASONING,
        extract_context="ctx",
    )
    assert result is original


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
