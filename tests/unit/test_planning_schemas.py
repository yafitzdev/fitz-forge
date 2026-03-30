# tests/unit/test_planning_schemas.py
"""Comprehensive tests for planning stage Pydantic schemas.

Covers default construction, full construction, validation,
serialization round-trips, and optional field handling for every
schema model in fitz_forge.planning.schemas.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError

from fitz_forge.planning.schemas.context import Assumption, ContextOutput
from fitz_forge.planning.schemas.architecture import Approach, ArchitectureOutput
from fitz_forge.planning.schemas.design import (
    ADR,
    Artifact,
    ComponentDesign,
    DesignOutput,
)
from fitz_forge.planning.schemas.roadmap import (
    Phase,
    PhaseRef,
    RoadmapOutput,
    _coerce_phase_number,
)
from fitz_forge.planning.schemas.risk import Risk, RiskOutput
from fitz_forge.planning.schemas.decisions import (
    AtomicDecision,
    DecisionDecompositionOutput,
    DecisionResolution,
    DecisionResolutionOutput,
)
from fitz_forge.planning.schemas.plan_output import PlanOutput


# ---- helpers ----

def _make_context(**overrides):
    defaults = {"project_description": "Test project"}
    defaults.update(overrides)
    return ContextOutput(**defaults)


def _make_architecture(**overrides):
    defaults = {"recommended": "Monolith", "reasoning": "Simple"}
    defaults.update(overrides)
    return ArchitectureOutput(**defaults)


def _make_plan(**overrides):
    defaults = {
        "context": _make_context(),
        "architecture": _make_architecture(),
        "design": DesignOutput(),
        "roadmap": RoadmapOutput(),
        "risk": RiskOutput(),
    }
    defaults.update(overrides)
    return PlanOutput(**defaults)


# =========================================================================
# Assumption
# =========================================================================


class TestAssumption:
    """Tests for Assumption sub-model."""

    def test_default_construction(self):
        a = Assumption(assumption="REST not GraphQL", impact="arch changes")
        assert a.confidence == "medium"

    def test_full_construction(self):
        a = Assumption(
            assumption="Single-tenant",
            impact="Data isolation changes",
            confidence="low",
        )
        assert a.assumption == "Single-tenant"
        assert a.impact == "Data isolation changes"
        assert a.confidence == "low"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            Assumption(assumption="only assumption, no impact")

    def test_round_trip(self):
        a = Assumption(assumption="X", impact="Y", confidence="high")
        restored = Assumption.model_validate(a.model_dump())
        assert restored == a

    def test_extra_fields_ignored(self):
        a = Assumption(
            assumption="X", impact="Y", unknown_extra="ignored"
        )
        assert a.assumption == "X"
        assert not hasattr(a, "unknown_extra")


# =========================================================================
# ContextOutput
# =========================================================================


class TestContextOutput:
    """Tests for ContextOutput schema."""

    def test_default_construction(self):
        c = ContextOutput()
        assert c.project_description == ""
        assert c.key_requirements == []
        assert c.constraints == []
        assert c.existing_context == ""
        assert c.stakeholders == []
        assert c.scope_boundaries == {}
        assert c.existing_files == []
        assert c.needed_artifacts == []
        assert c.assumptions == []

    def test_full_construction(self):
        c = ContextOutput(
            project_description="Build a REST API",
            key_requirements=["Auth", "CRUD"],
            constraints=["Must use Postgres"],
            existing_context="Legacy v1 exists",
            stakeholders=["Product", "Eng"],
            scope_boundaries={
                "in_scope": ["Users", "Tasks"],
                "out_of_scope": ["Billing"],
            },
            existing_files=["src/main.py"],
            needed_artifacts=["schema.sql"],
            assumptions=[
                Assumption(assumption="REST", impact="big", confidence="high")
            ],
        )
        assert c.project_description == "Build a REST API"
        assert len(c.key_requirements) == 2
        assert len(c.assumptions) == 1

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ContextOutput(key_requirements="not a list")

    def test_round_trip(self):
        c = ContextOutput(
            project_description="Test",
            key_requirements=["R1"],
            assumptions=[
                Assumption(assumption="A", impact="I"),
            ],
        )
        restored = ContextOutput.model_validate(c.model_dump())
        assert restored.project_description == c.project_description
        assert restored.assumptions[0].assumption == "A"

    def test_extra_fields_ignored(self):
        c = ContextOutput(project_description="X", bogus="val")
        assert c.project_description == "X"


# =========================================================================
# Approach
# =========================================================================


class TestApproach:
    """Tests for Approach sub-model."""

    def test_default_construction_requires_name_and_description(self):
        with pytest.raises(ValidationError):
            Approach()

    def test_minimal(self):
        a = Approach(name="Mono", description="Single app")
        assert a.pros == []
        assert a.cons == []
        assert a.complexity == "medium"
        assert a.best_for == []

    def test_full_construction(self):
        a = Approach(
            name="Microservices",
            description="Distributed",
            pros=["Scaling"],
            cons=["Complexity"],
            complexity="high",
            best_for=["Large orgs"],
        )
        assert a.name == "Microservices"
        assert len(a.pros) == 1

    def test_round_trip(self):
        a = Approach(name="X", description="Y", pros=["p"], complexity="low")
        restored = Approach.model_validate(a.model_dump())
        assert restored == a

    def test_extra_fields_ignored(self):
        a = Approach(name="X", description="Y", extra_stuff=123)
        assert a.name == "X"


# =========================================================================
# ArchitectureOutput
# =========================================================================


class TestArchitectureOutput:
    """Tests for ArchitectureOutput schema."""

    def test_requires_recommended_and_reasoning(self):
        with pytest.raises(ValidationError):
            ArchitectureOutput()

    def test_minimal(self):
        a = _make_architecture()
        assert a.approaches == []
        assert a.key_tradeoffs == {}
        assert a.technology_considerations == []
        assert a.scope_statement == ""

    def test_full_construction(self):
        a = ArchitectureOutput(
            approaches=[
                Approach(name="A", description="D"),
                Approach(name="B", description="D2"),
            ],
            recommended="A",
            reasoning="Because A is simpler",
            key_tradeoffs={"simplicity": "vs scalability"},
            technology_considerations=["Python", "FastAPI"],
            scope_statement="Small scope",
        )
        assert len(a.approaches) == 2
        assert a.key_tradeoffs["simplicity"] == "vs scalability"

    def test_key_tradeoffs_list_of_dicts_coerced(self):
        """LLMs sometimes produce key_tradeoffs as list of dicts."""
        a = ArchitectureOutput(
            recommended="X",
            reasoning="Y",
            key_tradeoffs=[
                {"tradeoff_name": "speed", "description": "vs correctness"},
                {"name": "cost", "description": "vs quality"},
            ],
        )
        assert a.key_tradeoffs == {
            "speed": "vs correctness",
            "cost": "vs quality",
        }

    def test_key_tradeoffs_list_of_dicts_without_name(self):
        """Coercer handles dicts missing both tradeoff_name and name."""
        a = ArchitectureOutput(
            recommended="X",
            reasoning="Y",
            key_tradeoffs=[
                {"description": "fallback name"},
            ],
        )
        assert "tradeoff_0" in a.key_tradeoffs

    def test_round_trip(self):
        a = _make_architecture(
            scope_statement="Narrow scope",
            technology_considerations=["Rust"],
        )
        restored = ArchitectureOutput.model_validate(a.model_dump())
        assert restored.scope_statement == "Narrow scope"
        assert restored.technology_considerations == ["Rust"]

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ArchitectureOutput(
                recommended=123,  # should be str
                reasoning="Y",
            )

    def test_extra_fields_ignored(self):
        a = ArchitectureOutput(
            recommended="X", reasoning="Y", hallucinated_field="gone"
        )
        assert a.recommended == "X"


# =========================================================================
# ADR
# =========================================================================


class TestADR:
    """Tests for ADR sub-model."""

    def test_requires_core_fields(self):
        with pytest.raises(ValidationError):
            ADR()

    def test_minimal(self):
        a = ADR(
            title="Use Postgres",
            context="Need storage",
            decision="Postgres",
            rationale="ACID",
        )
        assert a.consequences == []
        assert a.alternatives_considered == []

    def test_full_construction(self):
        a = ADR(
            title="Use Postgres",
            context="Need storage",
            decision="Postgres 15",
            rationale="Team expertise",
            consequences=["Hosting required"],
            alternatives_considered=["SQLite", "Mongo"],
        )
        assert len(a.alternatives_considered) == 2

    def test_round_trip(self):
        a = ADR(
            title="T", context="C", decision="D", rationale="R",
            consequences=["C1"], alternatives_considered=["A1"],
        )
        restored = ADR.model_validate(a.model_dump())
        assert restored == a


# =========================================================================
# Artifact
# =========================================================================


class TestArtifact:
    """Tests for Artifact sub-model."""

    def test_requires_filename_and_content(self):
        with pytest.raises(ValidationError):
            Artifact()

    def test_minimal(self):
        a = Artifact(filename="config.yaml", content="key: value")
        assert a.purpose == ""

    def test_full_construction(self):
        a = Artifact(
            filename="schema.sql",
            content="CREATE TABLE users (...);",
            purpose="Database schema",
        )
        assert a.filename == "schema.sql"
        assert "CREATE TABLE" in a.content
        assert a.purpose == "Database schema"

    def test_round_trip(self):
        a = Artifact(filename="f", content="c", purpose="p")
        restored = Artifact.model_validate(a.model_dump())
        assert restored == a


# =========================================================================
# ComponentDesign
# =========================================================================


class TestComponentDesign:
    """Tests for ComponentDesign sub-model."""

    def test_requires_name_and_purpose(self):
        with pytest.raises(ValidationError):
            ComponentDesign()

    def test_minimal(self):
        c = ComponentDesign(name="TaskService", purpose="Manage tasks")
        assert c.responsibilities == []
        assert c.interfaces == []
        assert c.dependencies == []

    def test_full_construction(self):
        c = ComponentDesign(
            name="AuthService",
            purpose="Handle authentication",
            responsibilities=["Login", "Register"],
            interfaces=["REST /auth"],
            dependencies=["Database"],
        )
        assert len(c.responsibilities) == 2

    def test_round_trip(self):
        c = ComponentDesign(
            name="N", purpose="P",
            responsibilities=["R"], interfaces=["I"], dependencies=["D"],
        )
        restored = ComponentDesign.model_validate(c.model_dump())
        assert restored == c


# =========================================================================
# DesignOutput
# =========================================================================


class TestDesignOutput:
    """Tests for DesignOutput schema."""

    def test_default_construction(self):
        d = DesignOutput()
        assert d.adrs == []
        assert d.components == []
        assert d.data_model == {}
        assert d.integration_points == []
        assert d.artifacts == []

    def test_full_construction(self):
        d = DesignOutput(
            adrs=[ADR(title="T", context="C", decision="D", rationale="R")],
            components=[ComponentDesign(name="N", purpose="P")],
            data_model={"User": ["id", "name"]},
            integration_points=["Slack API"],
            artifacts=[Artifact(filename="f.yaml", content="x: 1")],
        )
        assert len(d.adrs) == 1
        assert "User" in d.data_model

    def test_data_model_string_values_coerced(self):
        """field_validator coerces bare strings to single-element lists."""
        d = DesignOutput(
            data_model={"Entity": "str", "Other": ["a", "b"]}
        )
        assert d.data_model["Entity"] == ["str"]
        assert d.data_model["Other"] == ["a", "b"]

    def test_round_trip(self):
        d = DesignOutput(
            data_model={"Task": ["id", "title"]},
            integration_points=["API"],
        )
        restored = DesignOutput.model_validate(d.model_dump())
        assert restored.data_model == d.data_model

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            DesignOutput(adrs="not a list")

    def test_extra_fields_ignored(self):
        d = DesignOutput(ghost_field="boo")
        assert d.adrs == []


# =========================================================================
# Phase number coercion
# =========================================================================


class TestPhaseRefCoercion:
    """Tests for _coerce_phase_number and PhaseRef annotated type."""

    def test_int_passthrough(self):
        assert _coerce_phase_number(1) == 1

    def test_string_int(self):
        assert _coerce_phase_number("2") == 2

    def test_string_phase_prefix(self):
        assert _coerce_phase_number("Phase 3") == 3

    def test_string_phase_underscore(self):
        assert _coerce_phase_number("phase_0") == 0

    def test_no_digits_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _coerce_phase_number("no digits here")


# =========================================================================
# Phase
# =========================================================================


class TestPhase:
    """Tests for Phase sub-model."""

    def test_requires_number_and_name(self):
        with pytest.raises(ValidationError):
            Phase()

    def test_minimal(self):
        p = Phase(number=1, name="Setup")
        assert p.objective == ""
        assert p.deliverables == []
        assert p.dependencies == []
        assert p.estimated_complexity == "medium"
        assert p.key_risks == []
        assert p.verification_command == ""
        assert p.estimated_effort == ""

    def test_full_construction(self):
        p = Phase(
            number=2,
            name="Auth",
            objective="Implement auth",
            deliverables=["Login page", "JWT"],
            dependencies=[1],
            estimated_complexity="high",
            key_risks=["OAuth complexity"],
            verification_command="pytest tests/auth/",
            estimated_effort="~2 hours",
        )
        assert p.number == 2
        assert p.dependencies == [1]
        assert p.estimated_effort == "~2 hours"

    def test_num_field_normalized_to_number(self):
        """model_validator normalizes 'num' -> 'number'."""
        p = Phase(num=5, name="Test")
        assert p.number == 5

    def test_phase_number_coerced_from_string(self):
        """PhaseRef coerces string phase numbers."""
        p = Phase(number="Phase 3", name="Build")
        assert p.number == 3

    def test_dependencies_coerced_from_strings(self):
        """PhaseRef coercion applies to dependency list too."""
        p = Phase(number=2, name="X", dependencies=["Phase 1", "1"])
        assert p.dependencies == [1, 1]

    def test_round_trip(self):
        p = Phase(
            number=1, name="N", objective="O",
            deliverables=["D"], verification_command="pytest",
        )
        restored = Phase.model_validate(p.model_dump())
        assert restored == p

    def test_extra_fields_ignored(self):
        p = Phase(number=1, name="N", random_extra=True)
        assert p.number == 1


# =========================================================================
# RoadmapOutput
# =========================================================================


class TestRoadmapOutput:
    """Tests for RoadmapOutput schema."""

    def test_default_construction(self):
        r = RoadmapOutput()
        assert r.phases == []
        assert r.critical_path == []
        assert r.parallel_opportunities == []
        assert r.total_phases == 0

    def test_full_construction(self):
        r = RoadmapOutput(
            phases=[
                Phase(number=1, name="Setup"),
                Phase(number=2, name="Build", dependencies=[1]),
            ],
            critical_path=[1, 2],
            parallel_opportunities=[[1, 2]],
            total_phases=2,
        )
        assert len(r.phases) == 2
        assert r.total_phases == 2
        assert r.critical_path == [1, 2]

    def test_critical_path_coerces_strings(self):
        """PhaseRef coercion works in critical_path list."""
        r = RoadmapOutput(critical_path=["Phase 1", "Phase 2"])
        assert r.critical_path == [1, 2]

    def test_parallel_opportunities_coerce(self):
        r = RoadmapOutput(parallel_opportunities=[["Phase 1", "Phase 3"]])
        assert r.parallel_opportunities == [[1, 3]]

    def test_round_trip(self):
        r = RoadmapOutput(
            phases=[Phase(number=1, name="P1")],
            critical_path=[1],
            total_phases=1,
        )
        restored = RoadmapOutput.model_validate(r.model_dump())
        assert restored.total_phases == 1
        assert restored.phases[0].name == "P1"

    def test_extra_fields_ignored(self):
        r = RoadmapOutput(extra_field="gone")
        assert r.phases == []


# =========================================================================
# Risk
# =========================================================================


class TestRisk:
    """Tests for Risk sub-model."""

    def test_default_construction(self):
        r = Risk()
        assert r.category == "technical"
        assert r.description == ""
        assert r.impact == "medium"
        assert r.likelihood == "medium"
        assert r.mitigation == ""
        assert r.contingency == ""
        assert r.affected_phases == []
        assert r.verification == ""

    def test_full_construction(self):
        r = Risk(
            category="resource",
            description="Limited devops",
            impact="high",
            likelihood="high",
            mitigation="Use PaaS",
            contingency="Hire consultant",
            affected_phases=[3, 4],
            verification="terraform plan succeeds",
        )
        assert r.category == "resource"
        assert r.affected_phases == [3, 4]
        assert r.verification == "terraform plan succeeds"

    def test_desc_normalized_to_description(self):
        """model_validator normalizes 'desc' -> 'description'."""
        r = Risk(desc="short desc")
        assert r.description == "short desc"

    def test_phases_normalized_to_affected_phases(self):
        """model_validator normalizes 'phases' -> 'affected_phases'."""
        r = Risk(phases=[1, 2])
        assert r.affected_phases == [1, 2]

    def test_affected_phases_coerce_strings(self):
        """PhaseRef coercion applies to affected_phases."""
        r = Risk(affected_phases=["Phase 2", "3"])
        assert r.affected_phases == [2, 3]

    def test_round_trip(self):
        r = Risk(
            category="schedule",
            description="Tight deadline",
            impact="critical",
            verification="date check",
        )
        restored = Risk.model_validate(r.model_dump())
        assert restored == r

    def test_extra_fields_ignored(self):
        r = Risk(severity="critical")  # not a real field
        assert r.category == "technical"


# =========================================================================
# RiskOutput
# =========================================================================


class TestRiskOutput:
    """Tests for RiskOutput schema."""

    def test_default_construction(self):
        r = RiskOutput()
        assert r.risks == []
        assert r.overall_risk_level == "medium"
        assert r.recommended_contingencies == []

    def test_full_construction(self):
        r = RiskOutput(
            risks=[
                Risk(category="technical", description="API rate limits"),
            ],
            overall_risk_level="high",
            recommended_contingencies=["Budget buffer", "Hire help"],
        )
        assert len(r.risks) == 1
        assert r.overall_risk_level == "high"

    def test_round_trip(self):
        r = RiskOutput(
            risks=[Risk(description="test")],
            overall_risk_level="low",
            recommended_contingencies=["Plan B"],
        )
        restored = RiskOutput.model_validate(r.model_dump())
        assert restored.overall_risk_level == "low"
        assert len(restored.risks) == 1

    def test_extra_fields_ignored(self):
        r = RiskOutput(unknown=123)
        assert r.risks == []


# =========================================================================
# DecisionDecompositionOutput
# =========================================================================


class TestDecisionDecompositionOutput:
    """Tests for DecisionDecompositionOutput schema."""

    def test_default_construction(self):
        d = DecisionDecompositionOutput()
        assert d.decisions == []

    def test_full_construction(self):
        d = DecisionDecompositionOutput(
            decisions=[
                AtomicDecision(
                    id="d1", question="What pattern?",
                    relevant_files=["a.py"], depends_on=[], category="pattern",
                ),
                AtomicDecision(
                    id="d2", question="What interface?",
                    depends_on=["d1"], category="interface",
                ),
            ],
        )
        assert len(d.decisions) == 2
        assert d.decisions[1].depends_on == ["d1"]

    def test_round_trip(self):
        d = DecisionDecompositionOutput(
            decisions=[AtomicDecision(id="d1", question="Q")]
        )
        restored = DecisionDecompositionOutput.model_validate(d.model_dump())
        assert restored.decisions[0].id == "d1"


# =========================================================================
# DecisionResolutionOutput
# =========================================================================


class TestDecisionResolutionOutput:
    """Tests for DecisionResolutionOutput schema."""

    def test_default_construction(self):
        d = DecisionResolutionOutput()
        assert d.resolutions == []

    def test_full_construction(self):
        d = DecisionResolutionOutput(
            resolutions=[
                DecisionResolution(
                    decision_id="d1",
                    decision="Use X",
                    reasoning="Because Y",
                    evidence=["file.py:method()"],
                    constraints_for_downstream=["Must use X"],
                ),
            ],
        )
        assert len(d.resolutions) == 1
        assert d.resolutions[0].constraints_for_downstream == ["Must use X"]

    def test_round_trip(self):
        d = DecisionResolutionOutput(
            resolutions=[
                DecisionResolution(
                    decision_id="d1", decision="D", reasoning="R"
                )
            ]
        )
        restored = DecisionResolutionOutput.model_validate(d.model_dump())
        assert restored.resolutions[0].decision_id == "d1"


# =========================================================================
# PlanOutput
# =========================================================================


class TestPlanOutput:
    """Tests for PlanOutput aggregate schema."""

    def test_requires_stage_outputs(self):
        with pytest.raises(ValidationError):
            PlanOutput()

    def test_minimal(self):
        p = _make_plan()
        assert p.context.project_description == "Test project"
        assert p.architecture.recommended == "Monolith"
        assert isinstance(p.generated_at, datetime)
        assert p.job_description == ""
        assert p.git_sha == ""
        assert p.api_review_requested is False
        assert p.api_review_cost is None
        assert p.api_review_feedback is None
        assert p.diagnostics == {}

    def test_full_construction(self):
        p = PlanOutput(
            context=_make_context(project_description="Full plan"),
            architecture=_make_architecture(),
            design=DesignOutput(
                data_model={"User": ["id"]},
                artifacts=[Artifact(filename="f", content="c")],
            ),
            roadmap=RoadmapOutput(
                phases=[Phase(number=1, name="P1")],
                total_phases=1,
            ),
            risk=RiskOutput(overall_risk_level="low"),
            job_description="Build a thing",
            git_sha="abc123",
            api_review_requested=True,
            api_review_cost={"input_tokens": 100, "output_tokens": 50},
            api_review_feedback={"context": "Looks good"},
            diagnostics={"provider": "ollama", "model": "qwen"},
        )
        assert p.job_description == "Build a thing"
        assert p.api_review_requested is True
        assert p.api_review_cost["input_tokens"] == 100
        assert p.diagnostics["provider"] == "ollama"

    def test_round_trip(self):
        p = _make_plan(
            job_description="Round-trip test",
            git_sha="deadbeef",
            diagnostics={"calls": 5},
        )
        data = p.model_dump()
        restored = PlanOutput.model_validate(data)
        assert restored.job_description == "Round-trip test"
        assert restored.git_sha == "deadbeef"
        assert restored.diagnostics["calls"] == 5
        assert restored.context.project_description == "Test project"

    def test_extra_fields_ignored(self):
        p = _make_plan(phantom_field="gone")
        assert p.context.project_description == "Test project"

    def test_nested_extra_fields_ignored(self):
        """Extra fields on nested stage outputs are also ignored."""
        c = ContextOutput(
            project_description="P", hallucinated="ignored"
        )
        p = _make_plan(context=c)
        assert p.context.project_description == "P"

    def test_optional_api_review_fields(self):
        """api_review_cost and api_review_feedback accept None."""
        p = _make_plan(
            api_review_cost=None,
            api_review_feedback=None,
        )
        assert p.api_review_cost is None
        assert p.api_review_feedback is None

    def test_api_review_fields_populated(self):
        """api_review_cost and api_review_feedback accept dicts."""
        p = _make_plan(
            api_review_cost={"total": 0.01},
            api_review_feedback={"arch": "LGTM"},
        )
        assert p.api_review_cost["total"] == 0.01
        assert p.api_review_feedback["arch"] == "LGTM"


# =========================================================================
# __init__.py re-exports
# =========================================================================


class TestSchemaReExports:
    """Verify the public API exposed by the schemas __init__."""

    def test_all_exports_importable(self):
        from fitz_forge.planning.schemas import __all__

        expected = {
            "Assumption", "ContextOutput",
            "ArchitectureOutput", "Approach",
            "DesignOutput", "ADR", "Artifact", "ComponentDesign",
            "RoadmapOutput", "Phase", "PhaseRef",
            "RiskOutput", "Risk",
            "PlanOutput",
        }
        assert expected.issubset(set(__all__))
