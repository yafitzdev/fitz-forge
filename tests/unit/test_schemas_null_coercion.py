# tests/unit/test_schemas_null_coercion.py
"""Tests for LLMOutputModel null-to-default coercion.

Local models sometimes emit ``"field": null`` for optional string fields
even when the schema declares ``default=""``. Pydantic rejects null for
``str``-typed fields even with a default. LLMOutputModel's before-validator
coerces null → default so partial plans still validate.

Regression anchor: V2 Run 1 (streaming benchmark, 2026-04-18) crashed on
RiskOutput validation because two risks.verification fields came back
null. This suite locks in the coercer so that class of failure stays
fixed across every schema.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.schemas.architecture import Approach, ArchitectureOutput
from fitz_forge.planning.schemas.context import Assumption, ContextOutput
from fitz_forge.planning.schemas.decisions import AtomicDecision, DecisionResolution
from fitz_forge.planning.schemas.design import ADR, Artifact, ComponentDesign
from fitz_forge.planning.schemas.risk import Risk, RiskOutput
from fitz_forge.planning.schemas.roadmap import Phase


# ---------------------------------------------------------------------------
# Risk — the schema that actually crashed V2 Run 1
# ---------------------------------------------------------------------------


def test_risk_verification_null_coerced_to_default():
    risk = Risk(description="test", verification=None)
    assert risk.verification == ""


def test_risk_output_with_null_verifications_validates():
    """Reproduces V2 Run 1 failure shape — two null verification fields."""
    out = RiskOutput(
        risks=[
            {"description": "a", "verification": None},
            {"description": "b", "verification": None},
        ]
    )
    assert len(out.risks) == 2
    assert all(r.verification == "" for r in out.risks)


def test_risk_multiple_null_string_fields_all_coerced():
    """All string fields on Risk should tolerate null."""
    risk = Risk(
        category=None,
        description=None,
        impact=None,
        likelihood=None,
        mitigation=None,
        contingency=None,
        verification=None,
    )
    assert risk.description == ""
    assert risk.mitigation == ""
    assert risk.contingency == ""
    assert risk.verification == ""
    # fields with non-empty defaults keep their defaults
    assert risk.category == "technical"
    assert risk.impact == "medium"
    assert risk.likelihood == "medium"


# ---------------------------------------------------------------------------
# Every schema inheriting LLMOutputModel should tolerate null string fields
# ---------------------------------------------------------------------------


def _minimal_required(cls) -> dict:
    """Build a dict with all required fields set to a placeholder string."""
    out: dict = {}
    for name, info in cls.model_fields.items():
        if not info.is_required():
            continue
        ann = info.annotation
        if ann is str:
            out[name] = "x"
        elif ann is int:
            out[name] = 1
        elif ann is float:
            out[name] = 0.0
        else:
            out[name] = "x"  # best-effort stringish placeholder
    return out


@pytest.mark.parametrize(
    "cls,null_field,expected_default",
    [
        # Only test OPTIONAL string fields — required fields are the caller's
        # responsibility and the coercer is for partial-output robustness.
        (ArchitectureOutput, "scope_statement", ""),
        (ContextOutput, "problem_statement", ""),
        (ContextOutput, "success_criteria", ""),
        (DecisionResolution, "evidence_summary", ""),
        # ADR.consequences / ComponentDesign.responsibilities are list-typed;
        # the string-only coercer skips them. List-null robustness is out of
        # scope for this fix (no observed production crash yet).
        (Risk, "description", ""),
        (Risk, "mitigation", ""),
        (Risk, "contingency", ""),
        (Risk, "verification", ""),
        (Phase, "duration", ""),
    ],
)
def test_optional_string_field_with_null_value_coerced(cls, null_field, expected_default):
    if null_field not in cls.model_fields:
        pytest.skip(f"{cls.__name__} does not define {null_field}")
    kwargs = _minimal_required(cls)
    kwargs[null_field] = None
    obj = cls(**kwargs)
    assert getattr(obj, null_field) == expected_default


# ---------------------------------------------------------------------------
# Normal string values must not be disturbed
# ---------------------------------------------------------------------------


def test_normal_values_pass_through_unchanged():
    risk = Risk(
        description="real description",
        mitigation="real mitigation",
        verification="run pytest",
    )
    assert risk.description == "real description"
    assert risk.mitigation == "real mitigation"
    assert risk.verification == "run pytest"


def test_missing_field_still_uses_default():
    """Null-coercion must not break missing-field default behavior."""
    risk = Risk()
    assert risk.description == ""
    assert risk.verification == ""
    assert risk.category == "technical"


# ---------------------------------------------------------------------------
# Non-string fields retain their validation semantics
# ---------------------------------------------------------------------------


def test_non_string_null_is_not_silently_coerced():
    """Coercer targets strings only. Null on a list-typed field still
    raises — that's intentional scope for this fix (we only saw
    production crashes on string fields). Locking this in so a future
    broader coercer change is an explicit decision, not an accidental
    side effect."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        Risk(description="test", affected_phases=None)


# ---------------------------------------------------------------------------
# Nested construction: the top-level RiskOutput handles a mix of null + real
# ---------------------------------------------------------------------------


def test_risk_output_mixed_null_and_real():
    out = RiskOutput(
        overall_risk_level=None,  # string default "medium"
        risks=[
            {"description": "real", "verification": "real_cmd"},
            {"description": None, "verification": None},
        ],
    )
    assert out.overall_risk_level == "medium"
    assert out.risks[0].description == "real"
    assert out.risks[0].verification == "real_cmd"
    assert out.risks[1].description == ""
    assert out.risks[1].verification == ""
