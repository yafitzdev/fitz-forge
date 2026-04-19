# tests/unit/test_synthesis_senior_design_review_wiring.py
"""Tests for the synthesis stage's senior design review pass.

Runs before artifact generation: review_design flags under-specified
interfaces, rubric gaps, and missing components. When ``reasoning`` is
supplied, the affected design field groups (components/data_model,
adrs, integrations) are regenerated with the feedback appended and
whichever pass has fewer issues is kept. The review returns a tuple
``(design, artifact_feedback_issues)`` — the second value is what the
caller cascades into the per-file artifact generator's reasoning so
field-name precision from the review actually reaches the code.
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
# Empty design / no content → skip review, empty feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_design_skips(monkeypatch, stage):
    async def fake_review(**kwargs):  # pragma: no cover - must not fire
        raise AssertionError("review must not run on empty design")

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    original = _design(with_content=False)
    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert design is original
    assert issues == []


# ---------------------------------------------------------------------------
# Clean design → no findings attached, no artifact feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_design_no_findings(monkeypatch, stage):
    async def fake_review(**kwargs):
        return ReviewResult(scope="design", passed=True)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    original = _design()
    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert "review_findings" not in design
    assert issues == []


# ---------------------------------------------------------------------------
# Issues attached as review_findings AND cascaded as artifact feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issues_attached_and_cascaded(monkeypatch, stage):
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
    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    # No reasoning supplied → regen doesn't run → findings surfaced.
    assert "review_findings" in design
    assert len(design["review_findings"]) == 1
    assert design["review_findings"][0]["scope"] == "design"
    # Artifact feedback always contains the ORIGINAL issues.
    assert len(issues) == 1
    assert issues[0].target == "Ranker"


# ---------------------------------------------------------------------------
# Review errors fail-safe — empty cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_exception_keeps_original(monkeypatch, stage):
    async def exploding(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(syn_mod, "review_design", exploding)

    original = _design()
    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert design is original
    assert issues == []


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
    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
    )
    assert len(design["review_findings"]) == 2
    scopes = {f["scope"] for f in design["review_findings"]}
    assert scopes == {"other", "design"}
    assert len(issues) == 1


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


# ---------------------------------------------------------------------------
# Regeneration: component-target issue → components group re-extracted,
# retry has fewer issues → regen wins, artifact feedback = ORIGINAL issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_component_issue_triggers_regen_and_regen_wins(monkeypatch, stage):
    original = {
        "components": [
            {
                "name": "Ranker",
                "purpose": "Rank results",
                "responsibilities": [],
                "interfaces": ["rank(query, docs)"],
                "dependencies": [],
            }
        ],
        "data_model": {"Address": ["metadata"]},
        "artifacts": [],
        "adrs": [],
        "integration_points": [],
    }

    original_issues = [
        ReviewIssue(
            scope="design",
            target="Ranker",
            intent="Enumerate the five signal fields",
            actual="interface says 'rank' with no signal breakdown",
            suggestion="list base_score, strategy_weight, ...",
        )
    ]
    review_states = iter(
        [
            ReviewResult(scope="design", passed=False, issues=original_issues),
            ReviewResult(scope="design", passed=True),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    extract_calls = []

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        extract_calls.append(label)
        assert "after_review" in label
        return {
            "components": [
                {
                    "name": "Ranker",
                    "purpose": "Rank results with explicit signals",
                    "responsibilities": [],
                    "interfaces": [
                        "rank(query, docs) -> list[Scored]",
                        "record_signals(base_score, strategy_weight, ...)",
                    ],
                    "dependencies": [],
                }
            ],
            "data_model": {
                "Address": [
                    "base_score",
                    "strategy_weight",
                    "entity_bonus",
                    "keyword_boost",
                    "composite_score",
                ]
            },
        }

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
        reasoning="synthesis reasoning",
        extract_context="ctx",
    )

    assert any(lbl == "components_after_review" for lbl in extract_calls)
    # Regen won.
    assert "base_score" in design["data_model"]["Address"]
    # Retry passed → no review_findings attached.
    assert "review_findings" not in design
    # But artifact feedback is the ORIGINAL issues — the cascade into
    # artifact gen must still fire even when regen fully cleaned the
    # declared design, because the reasoning text artifact gen reads
    # is NOT the design.
    assert issues == original_issues


# ---------------------------------------------------------------------------
# Regeneration: data_model target routes to the components group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_model_target_routes_to_components_group(monkeypatch, stage):
    original = {
        "components": [
            {"name": "Engine", "purpose": "p", "interfaces": []},
        ],
        "data_model": {"Thing": ["id"]},
        "artifacts": [],
        "adrs": [],
        "integration_points": [],
    }

    review_states = iter(
        [
            ReviewResult(
                scope="design",
                passed=False,
                issues=[
                    ReviewIssue(
                        scope="design",
                        target="data_model",
                        intent="Name the fields",
                        actual="[metadata] only",
                        suggestion="Enumerate ...",
                    )
                ],
            ),
            ReviewResult(scope="design", passed=True),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    called_labels = []

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        called_labels.append(label)
        return {
            "components": original["components"],
            "data_model": {"Thing": ["id", "created_at"]},
        }

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
        reasoning="r",
        extract_context="",
    )

    assert called_labels == ["components_after_review"]


# ---------------------------------------------------------------------------
# Regeneration: artifact-filename target skips regen but still cascades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_target_skips_regen_still_cascades(monkeypatch, stage):
    original = {
        "components": [{"name": "X", "purpose": "p", "interfaces": []}],
        "data_model": {},
        "artifacts": [{"filename": "ranker.py", "content": "...", "purpose": "p"}],
        "adrs": [],
        "integration_points": [],
    }

    async def fake_review(**kwargs):
        return ReviewResult(
            scope="design",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="design",
                    target="ranker.py",
                    intent="Name ranking signals",
                    actual="writes single composite",
                    suggestion="Record each signal",
                )
            ],
        )

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    async def fake_extract(*args, **kwargs):  # pragma: no cover
        raise AssertionError("regen must not run for artifact-only issues")

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
        reasoning="r",
        extract_context="",
    )

    # No regen target → surface findings on design.
    assert design["review_findings"][0]["target"] == "ranker.py"
    # Artifact-filename issues still cascade into the artifact generator.
    assert len(issues) == 1
    assert issues[0].target == "ranker.py"


# ---------------------------------------------------------------------------
# Regeneration: retry does not improve → keep original and surface findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_no_improvement_surfaces_findings(monkeypatch, stage):
    original = {
        "components": [{"name": "X", "purpose": "p", "interfaces": []}],
        "data_model": {"T": ["id"]},
        "artifacts": [],
        "adrs": [],
        "integration_points": [],
    }

    issue = ReviewIssue(
        scope="design",
        target="X",
        intent="i",
        actual="a",
        suggestion="s",
    )
    review_states = iter(
        [
            ReviewResult(scope="design", passed=False, issues=[issue]),
            ReviewResult(
                scope="design",
                passed=False,
                issues=[
                    issue,
                    ReviewIssue(
                        scope="design",
                        target="X",
                        intent="i2",
                        actual="a2",
                        suggestion="s2",
                    ),
                ],
            ),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        return {
            "components": [{"name": "X", "purpose": "p", "interfaces": []}],
            "data_model": {"T": ["id"]},
        }

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
        reasoning="r",
        extract_context="",
    )

    assert design["data_model"] == {"T": ["id"]}
    assert any(f["target"] == "X" for f in design["review_findings"])
    assert issues == [issue]


# ---------------------------------------------------------------------------
# Regeneration: partial improvement → regen wins, remaining findings attached,
# artifact feedback still carries ORIGINAL issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_partial_improvement_attaches_remaining_findings(
    monkeypatch, stage
):
    original = {
        "components": [{"name": "C", "purpose": "p", "interfaces": []}],
        "data_model": {"E": ["id"]},
        "artifacts": [],
        "adrs": [],
        "integration_points": [],
    }
    original_issues = [
        ReviewIssue(
            scope="design",
            target="C",
            intent="i",
            actual="a",
            suggestion="s",
        ),
        ReviewIssue(
            scope="design",
            target="C",
            intent="i2",
            actual="a2",
            suggestion="s2",
        ),
    ]
    review_states = iter(
        [
            ReviewResult(scope="design", passed=False, issues=original_issues),
            ReviewResult(
                scope="design",
                passed=False,
                issues=[
                    ReviewIssue(
                        scope="design",
                        target="C",
                        intent="leftover",
                        actual="a",
                        suggestion="s",
                    )
                ],
            ),
        ]
    )

    async def fake_review(**kwargs):
        return next(review_states)

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        return {
            "components": [
                {"name": "C", "purpose": "p'", "interfaces": ["f(x)"]}
            ],
            "data_model": {"E": ["id", "name"]},
        }

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
        reasoning="r",
        extract_context="",
    )

    assert design["data_model"] == {"E": ["id", "name"]}
    assert design["review_findings"][0]["intent"] == "leftover"
    # Artifact feedback = ORIGINAL two issues, not the retry's remaining
    # one — the generator needs the full set to shape the code.
    assert issues == original_issues


# ---------------------------------------------------------------------------
# Regeneration: invalid re-extraction output → rolls back to original +
# surfaces findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_validation_failure_keeps_original(monkeypatch, stage):
    original = {
        "components": [{"name": "C", "purpose": "p", "interfaces": []}],
        "data_model": {"E": ["id"]},
        "artifacts": [],
        "adrs": [],
        "integration_points": [],
    }

    async def fake_review(**kwargs):
        return ReviewResult(
            scope="design",
            passed=False,
            issues=[
                ReviewIssue(
                    scope="design",
                    target="C",
                    intent="i",
                    actual="a",
                    suggestion="s",
                )
            ],
        )

    monkeypatch.setattr(syn_mod, "review_design", fake_review)

    async def fake_extract(client, reasoning, fields, schema, label, **kwargs):
        # Missing required `name` on the component → validation fails.
        return {
            "components": [{"purpose": "oops"}],
            "data_model": {"E": ["id"]},
        }

    monkeypatch.setattr(stage, "_extract_field_group", fake_extract)

    design, issues = await stage._senior_design_review_pass(
        client=object(),
        job_description="t",
        prior_outputs={},
        design=original,
        reasoning="r",
        extract_context="",
    )

    assert design["components"][0]["name"] == "C"
    assert design["review_findings"][0]["target"] == "C"
    assert len(issues) == 1
