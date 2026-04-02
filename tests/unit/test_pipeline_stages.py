# tests/unit/test_pipeline_stages.py
"""Comprehensive tests for pipeline stage modules.

Covers: DecisionDecompositionStage, DecisionResolutionStage,
SynthesisStage, ArtifactResolutionStage, and deeper coverage of
ContextStage, ArchitectureDesignStage, RoadmapRiskStage.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fitz_forge.planning.pipeline.stages.base import (
    PipelineStage,
    StageResult,
    extract_json,
)
from fitz_forge.planning.pipeline.stages.context import ContextStage
from fitz_forge.planning.pipeline.stages.architecture_design import (
    ArchitectureDesignStage,
)
from fitz_forge.planning.pipeline.stages.roadmap_risk import (
    RoadmapRiskStage,
    _remove_dependency_cycles,
)
from fitz_forge.planning.pipeline.stages.decision_decomposition import (
    DecisionDecompositionStage,
)
from fitz_forge.planning.pipeline.stages.decision_resolution import (
    DecisionResolutionStage,
    _topological_sort,
)
from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage
from fitz_forge.planning.pipeline.stages.artifact_resolution import (
    resolve_artifacts,
    _find_relevant_resolutions,
    _format_decisions,
    _get_source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(**overrides):
    """Build an AsyncMock LLM client with generate() and optional generate_with_tools()."""
    client = AsyncMock()
    for key, val in overrides.items():
        setattr(client, key, val)
    return client


# ---------------------------------------------------------------------------
# DecisionDecompositionStage
# ---------------------------------------------------------------------------

class TestDecisionDecompositionStageMetadata:
    """Stage metadata: name, progress_range."""

    def test_name(self):
        stage = DecisionDecompositionStage()
        assert stage.name == "decision_decomposition"

    def test_progress_range(self):
        stage = DecisionDecompositionStage()
        assert stage.progress_range == (0.10, 0.20)


class TestDecisionDecompositionBuildPrompt:
    """Prompt construction for decomposition."""

    def test_includes_job_description(self):
        stage = DecisionDecompositionStage()
        messages = stage.build_prompt("Build a chat plugin", {})
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Build a chat plugin" in messages[1]["content"]

    def test_includes_call_graph(self):
        stage = DecisionDecompositionStage()
        prior = {"_call_graph_text": "engine.py -> pipeline.py -> stages.py"}
        messages = stage.build_prompt("Task", prior)
        assert "engine.py -> pipeline.py" in messages[1]["content"]

    def test_includes_raw_summaries(self):
        stage = DecisionDecompositionStage()
        prior = {"_raw_summaries": "### engine.py\nclass Engine: manages pipeline"}
        messages = stage.build_prompt("Task", prior)
        assert "engine.py" in messages[1]["content"]

    def test_includes_implementation_check(self):
        """When already_implemented=True, the directive is injected into prompt."""
        stage = DecisionDecompositionStage()
        prior = {
            "_implementation_check": {
                "already_implemented": True,
                "evidence": "Found in engine.py",
                "gaps": ["missing streaming"],
            }
        }
        messages = stage.build_prompt("Task", prior)
        assert "EXISTING IMPLEMENTATION DETECTED" in messages[1]["content"]
        assert "Found in engine.py" in messages[1]["content"]


class TestDecisionDecompositionParseOutput:
    """Output parsing for decomposition."""

    def test_valid_json(self):
        stage = DecisionDecompositionStage()
        raw = json.dumps({
            "decisions": [
                {
                    "id": "d1",
                    "question": "What pattern to use?",
                    "relevant_files": ["engine.py"],
                    "depends_on": [],
                    "category": "pattern",
                },
                {
                    "id": "d2",
                    "question": "How to integrate?",
                    "relevant_files": ["api.py"],
                    "depends_on": ["d1"],
                    "category": "integration",
                },
            ]
        })
        result = stage.parse_output(raw)
        assert len(result["decisions"]) == 2
        assert result["decisions"][0]["id"] == "d1"
        assert result["decisions"][1]["depends_on"] == ["d1"]

    def test_empty_decisions(self):
        stage = DecisionDecompositionStage()
        raw = json.dumps({"decisions": []})
        result = stage.parse_output(raw)
        assert result["decisions"] == []


class TestDecisionDecompositionExecute:
    """Execute for decomposition stage."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """LLM returns valid decision list."""
        stage = DecisionDecompositionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(return_value=json.dumps({
            "decisions": [
                {
                    "id": "d1",
                    "question": "Which ORM to use?",
                    "relevant_files": ["models.py"],
                    "depends_on": [],
                    "category": "technical",
                },
                {
                    "id": "d2",
                    "question": "How to handle auth?",
                    "relevant_files": ["auth.py"],
                    "depends_on": ["d1"],
                    "category": "pattern",
                },
            ]
        }))

        result = await stage.execute(mock_client, "Build a REST API", {})

        assert result.success is True
        assert result.stage_name == "decision_decomposition"
        assert len(result.output["decisions"]) == 2
        assert result.output["decisions"][0]["id"] == "d1"

    @pytest.mark.asyncio
    async def test_removes_invalid_dependencies(self):
        """Dependencies referencing non-existent decisions are removed."""
        stage = DecisionDecompositionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(return_value=json.dumps({
            "decisions": [
                {
                    "id": "d1",
                    "question": "Q1?",
                    "depends_on": ["d_nonexistent"],
                },
            ]
        }))

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is True
        assert result.output["decisions"][0]["depends_on"] == []

    @pytest.mark.asyncio
    async def test_malformed_output_fails_gracefully(self):
        """Non-JSON output causes stage failure, not crash."""
        stage = DecisionDecompositionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            return_value="This is not JSON at all, just thinking..."
        )

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_llm_exception_fails_gracefully(self):
        """LLM client error produces a failed StageResult, not an exception."""
        stage = DecisionDecompositionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("Connection refused")
        )

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert "Connection refused" in result.error

    @pytest.mark.asyncio
    async def test_reports_substeps(self):
        """Reports 'decomposing' substep."""
        stage = DecisionDecompositionStage()
        reported = []

        async def track(phase: str) -> None:
            reported.append(phase)

        stage.set_substep_callback(track)

        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            return_value=json.dumps({"decisions": []})
        )

        await stage.execute(mock_client, "Task", {})
        assert "decision_decomposition:decomposing" in reported


# ---------------------------------------------------------------------------
# TopologicalSort (helper for DecisionResolutionStage)
# ---------------------------------------------------------------------------

class TestTopologicalSort:
    """Test topological ordering of decisions."""

    def test_linear_chain(self):
        decisions = [
            {"id": "d3", "depends_on": ["d2"]},
            {"id": "d1", "depends_on": []},
            {"id": "d2", "depends_on": ["d1"]},
        ]
        result = _topological_sort(decisions)
        ids = [d["id"] for d in result]
        assert ids.index("d1") < ids.index("d2")
        assert ids.index("d2") < ids.index("d3")

    def test_independent_decisions(self):
        decisions = [
            {"id": "d1", "depends_on": []},
            {"id": "d2", "depends_on": []},
            {"id": "d3", "depends_on": []},
        ]
        result = _topological_sort(decisions)
        assert len(result) == 3

    def test_diamond_dependency(self):
        decisions = [
            {"id": "d4", "depends_on": ["d2", "d3"]},
            {"id": "d1", "depends_on": []},
            {"id": "d2", "depends_on": ["d1"]},
            {"id": "d3", "depends_on": ["d1"]},
        ]
        result = _topological_sort(decisions)
        ids = [d["id"] for d in result]
        assert ids.index("d1") < ids.index("d2")
        assert ids.index("d1") < ids.index("d3")
        assert ids.index("d2") < ids.index("d4")
        assert ids.index("d3") < ids.index("d4")

    def test_cycle_handled(self):
        """Cycles are broken, all decisions still processed."""
        decisions = [
            {"id": "d1", "depends_on": ["d2"]},
            {"id": "d2", "depends_on": ["d1"]},
        ]
        result = _topological_sort(decisions)
        assert len(result) == 2
        ids = {d["id"] for d in result}
        assert ids == {"d1", "d2"}

    def test_deps_referencing_unknown_id_ignored(self):
        """Dependencies on IDs not in the list are safely ignored."""
        decisions = [
            {"id": "d1", "depends_on": ["d_ghost"]},
        ]
        result = _topological_sort(decisions)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# DecisionResolutionStage
# ---------------------------------------------------------------------------

class TestDecisionResolutionStageMetadata:
    """Stage metadata."""

    def test_name(self):
        stage = DecisionResolutionStage()
        assert stage.name == "decision_resolution"

    def test_progress_range(self):
        stage = DecisionResolutionStage()
        assert stage.progress_range == (0.20, 0.75)

    def test_build_prompt_raises(self):
        """build_prompt raises NotImplementedError (use _build_decision_prompt instead)."""
        stage = DecisionResolutionStage()
        with pytest.raises(NotImplementedError):
            stage.build_prompt("task", {})


class TestDecisionResolutionParseOutput:
    """Output parsing for resolution."""

    def test_valid_resolution(self):
        stage = DecisionResolutionStage()
        raw = json.dumps({
            "decision_id": "d1",
            "decision": "Use SQLAlchemy ORM",
            "reasoning": "Best fit for the project",
            "evidence": ["models.py: uses Base class"],
            "constraints_for_downstream": ["Must use async session"],
        })
        result = stage.parse_output(raw)
        assert result["decision_id"] == "d1"
        assert result["decision"] == "Use SQLAlchemy ORM"
        assert len(result["evidence"]) == 1
        assert len(result["constraints_for_downstream"]) == 1


class TestDecisionResolutionExecute:
    """Execute for resolution stage."""

    @pytest.mark.asyncio
    async def test_happy_path_two_decisions(self):
        """Resolves two decisions in topological order."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()

        # Two LLM calls: one per decision
        mock_client.generate = AsyncMock(side_effect=[
            json.dumps({
                "decision_id": "d1",
                "decision": "Use REST",
                "reasoning": "Simple and standard",
                "evidence": ["api.py: has REST routes"],
                "constraints_for_downstream": ["Must use HTTP verbs"],
            }),
            json.dumps({
                "decision_id": "d2",
                "decision": "Use JWT auth",
                "reasoning": "Stateless",
                "evidence": [],
                "constraints_for_downstream": [],
            }),
        ])

        prior = {
            "decision_decomposition": {
                "decisions": [
                    {"id": "d1", "question": "API style?", "depends_on": [], "relevant_files": []},
                    {"id": "d2", "question": "Auth?", "depends_on": ["d1"], "relevant_files": []},
                ]
            }
        }

        result = await stage.execute(mock_client, "Build API", prior)

        assert result.success is True
        assert result.stage_name == "decision_resolution"
        assert len(result.output["resolutions"]) == 2
        assert result.output["resolutions"][0]["decision_id"] == "d1"
        assert result.output["resolutions"][1]["decision_id"] == "d2"

    @pytest.mark.asyncio
    async def test_no_decisions_fails(self):
        """Empty decision list produces a failed result."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert "No decisions" in result.error

    @pytest.mark.asyncio
    async def test_malformed_resolution_uses_raw_text(self):
        """Unparseable LLM response falls back to raw text as decision."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            return_value="I think we should use REST because it's simple."
        )

        prior = {
            "decision_decomposition": {
                "decisions": [
                    {"id": "d1", "question": "API style?", "depends_on": [], "relevant_files": []},
                ]
            }
        }

        result = await stage.execute(mock_client, "Task", prior)

        assert result.success is True
        assert len(result.output["resolutions"]) == 1
        # Falls back to raw text
        assert "REST" in result.output["resolutions"][0]["decision"]

    @pytest.mark.asyncio
    async def test_upstream_constraints_injected(self):
        """Constraints from d1 are passed to d2's prompt."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()

        mock_client.generate = AsyncMock(side_effect=[
            json.dumps({
                "decision_id": "d1",
                "decision": "Use REST",
                "reasoning": "Standard",
                "evidence": [],
                "constraints_for_downstream": ["API must be RESTful"],
            }),
            json.dumps({
                "decision_id": "d2",
                "decision": "JSON format",
                "reasoning": "REST standard",
                "evidence": [],
                "constraints_for_downstream": [],
            }),
        ])

        prior = {
            "decision_decomposition": {
                "decisions": [
                    {"id": "d1", "question": "API style?", "depends_on": [], "relevant_files": []},
                    {"id": "d2", "question": "Format?", "depends_on": ["d1"], "relevant_files": []},
                ]
            }
        }

        await stage.execute(mock_client, "Task", prior)

        # Second call (d2) should contain the constraint from d1
        calls = mock_client.generate.call_args_list
        d2_prompt = calls[1].kwargs["messages"][1]["content"]
        assert "API must be RESTful" in d2_prompt

    @pytest.mark.asyncio
    async def test_crash_recovery_skips_resolved(self):
        """Already-resolved decisions (from checkpoint) are skipped."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()

        # Only one LLM call — d1 is already resolved
        mock_client.generate = AsyncMock(return_value=json.dumps({
            "decision_id": "d2",
            "decision": "Use JWT",
            "reasoning": "Stateless",
            "evidence": [],
            "constraints_for_downstream": [],
        }))

        prior = {
            "decision_decomposition": {
                "decisions": [
                    {"id": "d1", "question": "Q1?", "depends_on": [], "relevant_files": []},
                    {"id": "d2", "question": "Q2?", "depends_on": ["d1"], "relevant_files": []},
                ]
            },
            "_resolution_partial_d1": {
                "decision_id": "d1",
                "decision": "Use REST",
                "reasoning": "Standard",
                "evidence": [],
                "constraints_for_downstream": ["RESTful"],
            },
        }

        result = await stage.execute(mock_client, "Task", prior)

        assert result.success is True
        assert len(result.output["resolutions"]) == 2
        assert mock_client.generate.call_count == 1  # only d2

    @pytest.mark.asyncio
    async def test_file_contents_used_in_prompt(self):
        """File contents from prior_outputs are included in the decision prompt."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(return_value=json.dumps({
            "decision_id": "d1",
            "decision": "Extend Engine",
            "reasoning": "Has the right interface",
            "evidence": ["engine.py: class Engine"],
            "constraints_for_downstream": [],
        }))

        prior = {
            "decision_decomposition": {
                "decisions": [
                    {
                        "id": "d1",
                        "question": "Where to add streaming?",
                        "depends_on": [],
                        "relevant_files": ["engine.py"],
                    },
                ]
            },
            "_file_contents": {
                "engine.py": "class Engine:\n    def run(self): pass",
            },
        }

        await stage.execute(mock_client, "Task", prior)

        calls = mock_client.generate.call_args_list
        prompt = calls[0].kwargs["messages"][1]["content"]
        assert "class Engine" in prompt

    @pytest.mark.asyncio
    async def test_llm_exception_fails_gracefully(self):
        """Client exception produces failed result."""
        stage = DecisionResolutionStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("OOM")
        )

        prior = {
            "decision_decomposition": {
                "decisions": [
                    {"id": "d1", "question": "Q?", "depends_on": [], "relevant_files": []},
                ]
            }
        }

        result = await stage.execute(mock_client, "Task", prior)

        assert result.success is False
        assert "OOM" in result.error


# ---------------------------------------------------------------------------
# SynthesisStage
# ---------------------------------------------------------------------------

class TestSynthesisStageMetadata:
    """Stage metadata."""

    def test_name(self):
        stage = SynthesisStage()
        assert stage.name == "synthesis"

    def test_progress_range(self):
        stage = SynthesisStage()
        assert stage.progress_range == (0.75, 0.95)


class TestSynthesisBuildPrompt:
    """Prompt construction for synthesis."""

    def test_includes_resolutions(self):
        stage = SynthesisStage()
        prior = {
            "decision_resolution": {
                "resolutions": [
                    {
                        "decision_id": "d1",
                        "decision": "Use REST API",
                        "reasoning": "Standard approach",
                        "evidence": ["api.py: has routes"],
                        "constraints_for_downstream": [],
                    }
                ]
            }
        }
        messages = stage.build_prompt("Build API", prior)
        assert len(messages) == 2
        assert "Use REST API" in messages[1]["content"]

    def test_empty_resolutions(self):
        stage = SynthesisStage()
        messages = stage.build_prompt("Build API", {})
        assert len(messages) == 2
        # Should still have the system and user messages
        assert messages[0]["role"] == "system"


class TestSynthesisExecute:
    """Execute for synthesis stage."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Full synthesis with all field group extractions."""
        stage = SynthesisStage()
        mock_client = AsyncMock()

        # Responses: 1 synthesis + 1 critique + field groups
        # Context: description, stakeholders, files, assumptions (4)
        # Architecture: approaches, tradeoffs (2)
        # Design: adrs, components, integrations (3) + artifacts (1)
        # Roadmap: phases, scheduling (2)
        # Risk: risks (1)
        # Total: 2 synthesis (best-of-2) + 1 critique + 4 + 2 + 3 + 1 + 2 + 1 = 16
        mock_client.generate = AsyncMock(side_effect=[
            # 1a. Synthesis reasoning candidate 1
            "Comprehensive synthesis of all decisions into a coherent plan...",
            # 1b. Synthesis reasoning candidate 2
            "Alternative synthesis reasoning for best-of-2 selection...",
            # 2. Self-critique
            "Reviewed and refined synthesis...",
            # Context groups
            json.dumps({
                "project_description": "REST API for task management",
                "key_requirements": ["CRUD operations"],
                "constraints": ["Python 3.12+"],
                "existing_context": "",
            }),
            json.dumps({
                "stakeholders": ["Developers"],
                "scope_boundaries": {"in_scope": ["API"], "out_of_scope": ["UI"]},
            }),
            json.dumps({
                "existing_files": ["models.py"],
                "needed_artifacts": ["routes.py"],
            }),
            json.dumps({
                "assumptions": [],
            }),
            # Architecture groups
            json.dumps({
                "approaches": [
                    {"name": "Monolith", "description": "Single service",
                     "pros": ["Simple"], "cons": ["Scaling"],
                     "complexity": "low", "best_for": ["MVP"]},
                ],
                "recommended": "Monolith",
                "reasoning": "Best for MVP",
                "scope_statement": "Small REST API",
            }),
            json.dumps({
                "key_tradeoffs": {"simplicity": "vs scale"},
                "technology_considerations": ["FastAPI"],
            }),
            # Design groups (adrs, components, integrations)
            json.dumps({
                "adrs": [
                    {"title": "ADR: Use SQLite", "context": "Simple DB",
                     "decision": "SQLite", "rationale": "Embedded",
                     "consequences": ["No clustering"],
                     "alternatives_considered": ["PostgreSQL"]},
                ],
            }),
            json.dumps({
                "components": [
                    {"name": "Router", "purpose": "HTTP routing",
                     "responsibilities": ["route requests"],
                     "interfaces": ["GET /tasks"], "dependencies": ["DB"]},
                ],
                "data_model": {"Task": ["id: int", "title: str"]},
            }),
            json.dumps({
                "integration_points": ["SQLite database"],
            }),
            # Design: artifacts are now generated per-file (one call per needed artifact)
            # This replaces the old monolithic extraction
            json.dumps({
                "filename": "routes.py",
                "content": "from fastapi import APIRouter",
                "purpose": "API routes",
            }),
            # Roadmap groups
            json.dumps({
                "phases": [
                    {"number": 1, "name": "Setup", "objective": "Initialize project",
                     "deliverables": ["pyproject.toml"], "dependencies": [],
                     "estimated_complexity": "low", "key_risks": [],
                     "verification_command": "pytest tests/", "estimated_effort": "~1h"},
                ],
            }),
            json.dumps({
                "critical_path": [1],
                "parallel_opportunities": [],
                "total_phases": 1,
            }),
            # Risk group
            json.dumps({
                "risks": [
                    {"category": "technical", "description": "SQLite concurrency",
                     "impact": "medium", "likelihood": "low",
                     "mitigation": "Use WAL mode", "contingency": "Switch to Postgres",
                     "affected_phases": [1], "verification": "test concurrent writes"},
                ],
                "overall_risk_level": "low",
                "recommended_contingencies": ["Have Postgres fallback"],
            }),
        ])

        result = await stage.execute(mock_client, "Build a task management API", {})

        assert result.success is True
        assert result.stage_name == "synthesis"

        # Verify output structure
        assert "context" in result.output
        assert "architecture" in result.output
        assert "design" in result.output
        assert "roadmap" in result.output
        assert "risk" in result.output

        # Verify content
        assert result.output["context"]["project_description"] == "REST API for task management"
        assert result.output["architecture"]["recommended"] == "Monolith"
        assert len(result.output["design"]["adrs"]) == 1
        assert len(result.output["roadmap"]["phases"]) == 1
        assert result.output["risk"]["overall_risk_level"] == "low"

    @pytest.mark.asyncio
    async def test_all_extractions_fail_still_succeeds(self):
        """All field group extractions failing still produces a skeleton plan."""
        stage = SynthesisStage()
        mock_client = AsyncMock()

        # 1 synthesis + 1 critique + 13 field groups all fail
        responses = [
            "Synthesis reasoning...",
            "Critique...",
        ] + ["not json"] * 13

        mock_client.generate = AsyncMock(side_effect=responses)

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is True
        # All sections present with defaults
        assert result.output["context"]["project_description"] == ""
        assert result.output["architecture"]["approaches"] == []
        assert result.output["design"]["adrs"] == []
        assert result.output["roadmap"]["phases"] == []
        assert result.output["risk"]["risks"] == []

    @pytest.mark.asyncio
    async def test_llm_exception_fails_gracefully(self):
        """Client exception on synthesis reasoning produces failed result."""
        stage = SynthesisStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("Model crashed")
        )

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert "Model crashed" in result.error

    @pytest.mark.asyncio
    async def test_reports_substeps(self):
        """Reports synthesizing + critiquing + extracting substeps."""
        stage = SynthesisStage()
        reported = []

        async def track(phase: str) -> None:
            reported.append(phase)

        stage.set_substep_callback(track)

        mock_client = AsyncMock()
        # synthesis + critique + 13 field groups
        responses = [
            "Synthesis...",
            "Critique...",
        ]
        # 4 context + 2 arch + 3 design (excl artifacts) + artifact + 2 roadmap + 1 risk = 13
        for _ in range(13):
            responses.append(json.dumps({}))

        mock_client.generate = AsyncMock(side_effect=responses)

        await stage.execute(mock_client, "Task", {})
        assert "synthesis:synthesizing" in reported
        assert "synthesis:critiquing" in reported


# ---------------------------------------------------------------------------
# ArtifactResolution (function-based, not a stage class)
# ---------------------------------------------------------------------------

class TestFindRelevantResolutions:
    """Test resolution-to-file matching."""

    def test_matches_by_evidence_path(self):
        resolutions = [
            {"decision_id": "d1", "evidence": ["engine.py: class Engine"], "question": ""},
            {"decision_id": "d2", "evidence": ["api.py: routes"], "question": ""},
        ]
        result = _find_relevant_resolutions("engine.py", resolutions)
        assert len(result) == 1
        assert result[0]["decision_id"] == "d1"

    def test_matches_by_basename(self):
        resolutions = [
            {"decision_id": "d1", "evidence": ["src/engine.py: class Engine"], "question": ""},
        ]
        result = _find_relevant_resolutions("engine.py", resolutions)
        assert len(result) == 1

    def test_matches_by_question(self):
        resolutions = [
            {"decision_id": "d1", "evidence": [], "question": "How to modify engine.py?"},
        ]
        result = _find_relevant_resolutions("engine.py", resolutions)
        assert len(result) == 1

    def test_no_match(self):
        resolutions = [
            {"decision_id": "d1", "evidence": ["api.py: routes"], "question": "What about api?"},
        ]
        result = _find_relevant_resolutions("engine.py", resolutions)
        assert len(result) == 0


class TestFormatDecisions:
    """Test decision formatting for artifact prompts."""

    def test_formats_with_evidence_and_constraints(self):
        resolutions = [
            {
                "decision_id": "d1",
                "decision": "Use streaming",
                "evidence": ["engine.py: def run()"],
                "constraints_for_downstream": ["Must be async"],
            },
        ]
        text = _format_decisions(resolutions)
        assert "d1" in text
        assert "Use streaming" in text
        assert "engine.py: def run()" in text
        assert "Must be async" in text

    def test_empty_list(self):
        text = _format_decisions([])
        assert text == ""


class TestGetSource:
    """Test source code retrieval with fallbacks."""

    def test_direct_match(self):
        result = _get_source(
            "engine.py",
            {"engine.py": "class Engine: pass"},
            {},
            None,
        )
        assert "class Engine" in result

    def test_partial_match(self):
        result = _get_source(
            "engine.py",
            {"src/engine.py": "class Engine: pass"},
            {},
            None,
        )
        assert "class Engine" in result

    def test_index_fallback(self):
        result = _get_source(
            "engine.py",
            {},
            {"engine.py": "classes: Engine [run, stop]"},
            None,
        )
        assert "structural overview" in result
        assert "Engine" in result

    def test_no_source_new_file(self):
        result = _get_source("new_file.py", {}, {}, None)
        assert "new file" in result


class TestResolveArtifacts:
    """Test the resolve_artifacts function."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Generates one artifact per needed file."""
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(return_value=json.dumps({
            "filename": "routes.py",
            "content": "from fastapi import APIRouter\nrouter = APIRouter()",
            "purpose": "API routing",
        }))

        prior = {
            "decision_resolution": {
                "resolutions": [
                    {
                        "decision_id": "d1",
                        "decision": "Add routes",
                        "reasoning": "Need API",
                        "evidence": ["api.py: has base router"],
                        "constraints_for_downstream": ["Use FastAPI"],
                    },
                ],
            },
            "context": {
                "needed_artifacts": ["routes.py -- API routing module"],
            },
        }

        artifacts = await resolve_artifacts(mock_client, "Build API", prior)

        assert len(artifacts) == 1
        assert artifacts[0]["filename"] == "routes.py"
        assert "APIRouter" in artifacts[0]["content"]

    @pytest.mark.asyncio
    async def test_no_resolutions_returns_empty(self):
        """No resolutions means no artifacts."""
        mock_client = AsyncMock()
        artifacts = await resolve_artifacts(mock_client, "Task", {})
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_no_needed_artifacts_returns_empty(self):
        """No needed_artifacts in context means no artifacts."""
        mock_client = AsyncMock()
        prior = {
            "decision_resolution": {
                "resolutions": [
                    {"decision_id": "d1", "decision": "x", "reasoning": "y",
                     "evidence": [], "constraints_for_downstream": []},
                ],
            },
            "context": {"needed_artifacts": []},
        }
        artifacts = await resolve_artifacts(mock_client, "Task", prior)
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error_artifact(self):
        """LLM failure produces an artifact with error message, not crash."""
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("Model OOM")
        )

        prior = {
            "decision_resolution": {
                "resolutions": [
                    {"decision_id": "d1", "decision": "x", "reasoning": "y",
                     "evidence": [], "constraints_for_downstream": []},
                ],
            },
            "context": {
                "needed_artifacts": ["output.py -- generated code"],
            },
        }

        artifacts = await resolve_artifacts(mock_client, "Task", prior)

        assert len(artifacts) == 1
        assert "failed" in artifacts[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_caps_at_five_artifacts(self):
        """Never generates more than 5 artifacts."""
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(return_value=json.dumps({
            "filename": "f.py",
            "content": "# code",
            "purpose": "test",
        }))

        prior = {
            "decision_resolution": {
                "resolutions": [
                    {"decision_id": "d1", "decision": "x", "reasoning": "y",
                     "evidence": [], "constraints_for_downstream": []},
                ],
            },
            "context": {
                "needed_artifacts": [f"file_{i}.py -- module {i}" for i in range(10)],
            },
        }

        artifacts = await resolve_artifacts(mock_client, "Task", prior)

        assert len(artifacts) == 5
        assert mock_client.generate.call_count == 5


# ---------------------------------------------------------------------------
# ContextStage — deeper coverage
# ---------------------------------------------------------------------------

class TestContextStageDeeper:
    """Additional ContextStage tests beyond test_stages.py."""

    @pytest.fixture
    def stage(self):
        return ContextStage()

    def test_parse_output_with_assumptions(self, stage):
        """parse_output handles assumptions field."""
        raw = json.dumps({
            "project_description": "A task tracker",
            "key_requirements": ["CRUD"],
            "constraints": [],
            "existing_context": "",
            "stakeholders": [],
            "scope_boundaries": {},
            "existing_files": [],
            "needed_artifacts": [],
            "assumptions": [
                {"assumption": "REST not GraphQL", "impact": "Architecture changes", "confidence": "medium"},
            ],
        })
        result = stage.parse_output(raw)
        assert len(result["assumptions"]) == 1
        assert result["assumptions"][0]["assumption"] == "REST not GraphQL"

    def test_parse_output_minimal_input(self, stage):
        """parse_output fills defaults for missing fields."""
        raw = json.dumps({
            "project_description": "Minimal project",
        })
        result = stage.parse_output(raw)
        assert result["project_description"] == "Minimal project"
        assert result["key_requirements"] == []
        assert result["constraints"] == []

    @pytest.mark.asyncio
    async def test_execute_with_implementation_check(self, stage):
        """Implementation check is injected into prompt."""
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(side_effect=[
            "Reasoning with impl check...",
            "Critique...",
            json.dumps({"project_description": "P", "key_requirements": [], "constraints": [], "existing_context": ""}),
            json.dumps({"stakeholders": [], "scope_boundaries": {}}),
            json.dumps({"existing_files": [], "needed_artifacts": []}),
            json.dumps({"assumptions": []}),
        ])

        prior = {
            "_implementation_check": {
                "already_implemented": True,
                "evidence": "Found in engine.py",
                "gaps": [],
            }
        }

        result = await stage.execute(mock_client, "Task", prior)
        assert result.success is True

        # The reasoning call should have the impl check in its prompt
        calls = mock_client.generate.call_args_list
        first_call_messages = calls[0].kwargs.get("messages", calls[0].args[0] if calls[0].args else [])
        # Implementation check content should be in the prompt
        prompt_content = str(first_call_messages)
        assert "already_implemented" in prompt_content or "Found in engine.py" in prompt_content

    @pytest.mark.asyncio
    async def test_execute_complete_failure(self, stage):
        """Exception during execute produces failed StageResult."""
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("Connection lost")
        )

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert "Connection lost" in result.error


# ---------------------------------------------------------------------------
# ArchitectureDesignStage — deeper coverage
# ---------------------------------------------------------------------------

class TestArchitectureDesignStageDeeper:
    """Additional ArchitectureDesignStage tests."""

    def test_split_reasoning_init(self):
        """split_reasoning mode can be enabled via constructor."""
        stage = ArchitectureDesignStage(split_reasoning=True)
        assert stage._split_reasoning is True

    def test_default_not_split(self):
        stage = ArchitectureDesignStage()
        assert stage._split_reasoning is False

    def test_parse_output_missing_design_fields(self):
        """parse_output fills design defaults for missing fields."""
        stage = ArchitectureDesignStage()
        raw = json.dumps({
            "approaches": [
                {"name": "Simple", "description": "Basic approach",
                 "pros": ["Fast"], "cons": ["Limited"],
                 "complexity": "low", "best_for": ["MVP"]},
            ],
            "recommended": "Simple",
            "reasoning": "Good enough",
            "key_tradeoffs": {},
            "technology_considerations": [],
            "scope_statement": "Small project",
            # Missing all design fields
        })
        result = stage.parse_output(raw)
        assert result["design"]["adrs"] == []
        assert result["design"]["components"] == []
        assert result["design"]["data_model"] == {}
        assert result["design"]["integration_points"] == []
        assert result["design"]["artifacts"] == []

    def test_parse_output_missing_arch_fields(self):
        """parse_output fills architecture defaults for missing fields."""
        stage = ArchitectureDesignStage()
        raw = json.dumps({
            "approaches": [],
            "recommended": "",
            "reasoning": "",
            # Missing key_tradeoffs, technology_considerations, scope_statement
            "adrs": [],
            "components": [],
            "data_model": {},
            "integration_points": [],
            "artifacts": [],
        })
        result = stage.parse_output(raw)
        assert result["architecture"]["key_tradeoffs"] == {}
        assert result["architecture"]["technology_considerations"] == []

    def test_build_prompt_with_artifact_duplicates(self):
        """Artifact duplicate warnings are included in prompt."""
        stage = ArchitectureDesignStage()
        prior = {
            "_artifact_duplicates": [
                {
                    "proposed": "new_router.py",
                    "keywords": ["router", "api"],
                    "existing_matches": ["src/api/router.py"],
                },
            ],
        }
        messages = stage.build_prompt("Build API", prior)
        content = messages[1]["content"]
        assert "EXISTING CODE DETECTED" in content
        assert "new_router.py" in content

    @pytest.mark.asyncio
    async def test_execute_split_reasoning(self):
        """Split reasoning mode makes two sequential reasoning calls."""
        stage = ArchitectureDesignStage(split_reasoning=True)
        mock_client = AsyncMock()

        # Split mode: arch reasoning + design reasoning + critique + 6 field groups + ADR validator
        mock_client.generate = AsyncMock(side_effect=[
            # Architecture reasoning
            "Architecture analysis: recommend monolith...",
            # Design reasoning
            "Design decisions: use SQLite, JWT auth...",
            # Critique (on combined)
            "Reviewed combined reasoning...",
            # 6 field groups
            json.dumps({
                "approaches": [{"name": "Mono", "description": "Single",
                                "pros": [], "cons": [], "complexity": "low", "best_for": []}],
                "recommended": "Mono", "reasoning": "Simple", "scope_statement": "Small",
            }),
            json.dumps({"key_tradeoffs": {}, "technology_considerations": []}),
            json.dumps({"adrs": []}),
            json.dumps({"components": [], "data_model": {}}),
            json.dumps({"integration_points": []}),
            json.dumps({"artifacts": []}),
            # ADR validator
            json.dumps([
                {"title": "ADR: Generated", "context": "c", "decision": "d",
                 "rationale": "r", "consequences": [], "alternatives_considered": []},
                {"title": "ADR: Generated 2", "context": "c2", "decision": "d2",
                 "rationale": "r2", "consequences": [], "alternatives_considered": []},
            ]),
        ])

        result = await stage.execute(mock_client, "Build API", {})

        assert result.success is True
        assert "architecture" in result.output
        assert "design" in result.output
        # Raw output should contain both arch and design sections
        assert "Architecture Analysis" in result.raw_output
        assert "Design Decisions" in result.raw_output

    @pytest.mark.asyncio
    async def test_execute_total_failure(self):
        """Exception in execute produces failed StageResult."""
        stage = ArchitectureDesignStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("GPU OOM")
        )

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert "GPU OOM" in result.error


# ---------------------------------------------------------------------------
# RoadmapRiskStage — deeper coverage
# ---------------------------------------------------------------------------

class TestRoadmapRiskStageDeeper:
    """Additional RoadmapRiskStage tests."""

    def test_split_reasoning_init(self):
        """split_reasoning mode can be enabled."""
        stage = RoadmapRiskStage(split_reasoning=True)
        assert stage._split_reasoning is True

    def test_default_not_split(self):
        stage = RoadmapRiskStage()
        assert stage._split_reasoning is False

    def test_parse_output_num_field_normalization(self):
        """'num' field is normalized to 'number'."""
        stage = RoadmapRiskStage()
        raw = json.dumps({
            "phases": [
                {"num": 1, "name": "Setup", "objective": "Init",
                 "deliverables": [], "dependencies": [],
                 "estimated_complexity": "low", "key_risks": []},
            ],
            "critical_path": [1],
            "parallel_opportunities": [],
            "total_phases": 1,
            "risks": [],
            "overall_risk_level": "low",
            "recommended_contingencies": [],
        })
        result = stage.parse_output(raw)
        assert result["roadmap"]["phases"][0]["number"] == 1

    def test_parse_output_defaults(self):
        """Missing fields get sensible defaults."""
        stage = RoadmapRiskStage()
        raw = json.dumps({})
        result = stage.parse_output(raw)
        assert result["roadmap"]["phases"] == []
        assert result["roadmap"]["critical_path"] == []
        assert result["risk"]["overall_risk_level"] == "medium"
        assert result["risk"]["risks"] == []

    def test_build_prompt_with_full_prior(self):
        """Prompt includes context + architecture + design details."""
        stage = RoadmapRiskStage()
        prior = {
            "context": {
                "project_description": "Task tracker",
                "key_requirements": ["CRUD"],
                "constraints": ["Python"],
                "scope_boundaries": {"in_scope": ["API"]},
                "needed_artifacts": ["routes.py"],
            },
            "architecture": {
                "recommended": "Monolith",
                "reasoning": "Simple",
                "key_tradeoffs": {"speed": "vs scale"},
            },
            "design": {
                "components": [
                    {"name": "Router", "purpose": "Routing",
                     "interfaces": ["GET /tasks"], "dependencies": []},
                ],
                "adrs": [
                    {"title": "Use JWT", "decision": "JWT tokens",
                     "rationale": "Stateless"},
                ],
                "integration_points": ["Database"],
                "artifacts": [
                    {"filename": "routes.py", "content": "...", "purpose": "API"},
                ],
            },
        }
        messages = stage.build_prompt("Build tracker", prior)
        content = messages[1]["content"]
        assert "Monolith" in content
        assert "Router" in content
        assert "JWT" in content
        assert "Database" in content
        assert "routes.py" in content

    @pytest.mark.asyncio
    async def test_execute_split_reasoning(self):
        """Split reasoning mode makes two sequential calls."""
        stage = RoadmapRiskStage(split_reasoning=True)
        mock_client = AsyncMock()

        mock_client.generate = AsyncMock(side_effect=[
            # Roadmap reasoning
            "Roadmap: Phase 1 setup, Phase 2 core...",
            # Risk reasoning
            "Risk analysis: technical risk medium...",
            # Critique
            "Reviewed combined reasoning...",
            # 3 field groups
            json.dumps({
                "phases": [
                    {"number": 1, "name": "Setup", "objective": "Init",
                     "deliverables": ["project skeleton"], "dependencies": [],
                     "estimated_complexity": "low", "key_risks": [],
                     "verification_command": "pytest tests/test_setup.py -v",
                     "estimated_effort": "~1h"},
                ],
            }),
            json.dumps({
                "critical_path": [1],
                "parallel_opportunities": [],
                "total_phases": 1,
            }),
            json.dumps({
                "risks": [],
                "overall_risk_level": "low",
                "recommended_contingencies": [],
            }),
        ])

        result = await stage.execute(mock_client, "Build", {})

        assert result.success is True
        assert "roadmap" in result.output
        assert "risk" in result.output
        assert "Roadmap" in result.raw_output
        assert "Risk Assessment" in result.raw_output

    @pytest.mark.asyncio
    async def test_execute_total_failure(self):
        """Exception produces failed StageResult."""
        stage = RoadmapRiskStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("Timeout")
        )

        result = await stage.execute(mock_client, "Task", {})

        assert result.success is False
        assert "Timeout" in result.error


# ---------------------------------------------------------------------------
# StageResult dataclass
# ---------------------------------------------------------------------------

class TestStageResult:
    """Test the StageResult dataclass."""

    def test_success_result(self):
        result = StageResult(
            stage_name="test",
            success=True,
            output={"key": "value"},
            raw_output="raw text",
        )
        assert result.stage_name == "test"
        assert result.success is True
        assert result.output == {"key": "value"}
        assert result.error is None

    def test_failure_result(self):
        result = StageResult(
            stage_name="test",
            success=False,
            output={},
            raw_output="",
            error="Something broke",
        )
        assert result.success is False
        assert result.error == "Something broke"


# ---------------------------------------------------------------------------
# Base class helper methods
# ---------------------------------------------------------------------------

class TestBaseClassHelpers:
    """Test helper methods on PipelineStage base class."""

    def test_get_gathered_context(self):
        """Returns _gathered_context from prior_outputs."""
        stage = ContextStage()
        prior = {"_gathered_context": "## Code overview\nclass Engine"}
        assert "Code overview" in stage._get_gathered_context(prior)

    def test_get_gathered_context_missing(self):
        """Returns empty string when no context available."""
        stage = ContextStage()
        assert stage._get_gathered_context({}) == ""

    def test_get_raw_summaries(self):
        """Returns _raw_summaries from prior_outputs."""
        stage = ContextStage()
        prior = {"_raw_summaries": "### engine.py\nclass Engine: pass"}
        assert "engine.py" in stage._get_raw_summaries(prior)

    def test_get_raw_summaries_falls_back_to_gathered(self):
        """Falls back to gathered_context when raw_summaries missing."""
        stage = ContextStage()
        prior = {"_gathered_context": "## Gathered overview"}
        result = stage._get_raw_summaries(prior)
        assert "Gathered overview" in result

    def test_get_implementation_check_already_implemented(self):
        """Returns formatted directive when already_implemented=True."""
        stage = ContextStage()
        prior = {
            "_implementation_check": {
                "already_implemented": True,
                "evidence": "Found in engine.py",
                "gaps": ["missing feature X"],
            }
        }
        result = stage._get_implementation_check(prior)
        assert "EXISTING IMPLEMENTATION DETECTED" in result
        assert "Found in engine.py" in result
        assert "missing feature X" in result

    def test_get_implementation_check_not_implemented(self):
        """Returns empty string when already_implemented=False."""
        stage = ContextStage()
        prior = {
            "_implementation_check": {
                "already_implemented": False,
                "evidence": "Not found",
                "gaps": [],
            }
        }
        assert stage._get_implementation_check(prior) == ""

    def test_get_implementation_check_missing(self):
        """Returns empty string when no implementation check."""
        stage = ContextStage()
        assert stage._get_implementation_check({}) == ""

    def test_make_messages(self):
        """_make_messages returns system + user message pair."""
        stage = ContextStage()
        messages = stage._make_messages("Hello world")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_substep_callback_fires(self):
        """set_substep_callback enables substep reporting."""
        stage = ContextStage()
        reported = []

        async def track(phase: str) -> None:
            reported.append(phase)

        stage.set_substep_callback(track)
        await stage._report_substep("test_step")
        assert "context:test_step" in reported

    @pytest.mark.asyncio
    async def test_substep_callback_none(self):
        """No callback set means no error on _report_substep."""
        stage = ContextStage()
        # Should not raise
        await stage._report_substep("test_step")


# ---------------------------------------------------------------------------
# Self-critique behavior
# ---------------------------------------------------------------------------

class TestSelfCritique:
    """Test the _self_critique method on PipelineStage."""

    @pytest.mark.asyncio
    async def test_accepts_refined_output(self):
        """Refined output is accepted when it's long enough."""
        stage = ContextStage()
        mock_client = AsyncMock()
        original = "A" * 3000
        refined = "B" * 2500
        mock_client.generate = AsyncMock(return_value=refined)

        result = await stage._self_critique(mock_client, original, "Task")
        assert result == refined

    @pytest.mark.asyncio
    async def test_rejects_short_output(self):
        """Critique output that's too short is rejected, original kept."""
        stage = ContextStage()
        mock_client = AsyncMock()
        original = "A" * 10000
        # Very short — below both ratio threshold and absolute floor
        refined = "OK"
        mock_client.generate = AsyncMock(return_value=refined)

        result = await stage._self_critique(mock_client, original, "Task")
        assert result == original

    @pytest.mark.asyncio
    async def test_client_error_returns_original(self):
        """Client exception returns original reasoning unchanged."""
        stage = ContextStage()
        mock_client = AsyncMock()
        original = "Original reasoning text"
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )

        result = await stage._self_critique(mock_client, original, "Task")
        assert result == original

    @pytest.mark.asyncio
    async def test_includes_krag_context_in_prompt(self):
        """krag_context is injected into critique prompt."""
        stage = ContextStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(return_value="A" * 3000)

        await stage._self_critique(
            mock_client, "reasoning", "Task",
            krag_context="## Codebase\ndef important_function(): pass",
        )

        calls = mock_client.generate.call_args_list
        prompt = calls[0].kwargs["messages"][1]["content"]
        assert "important_function" in prompt
        assert "ACTUAL CODEBASE" in prompt


# ---------------------------------------------------------------------------
# Devil's advocate behavior
# ---------------------------------------------------------------------------

class TestDevilsAdvocate:
    """Test the _devil_advocate method on PipelineStage."""

    @pytest.mark.asyncio
    async def test_skips_without_context(self):
        """Returns original when no krag_context provided."""
        stage = ContextStage()
        mock_client = AsyncMock()
        original = "My reasoning"

        result = await stage._devil_advocate(mock_client, original, "Task")
        assert result == original
        mock_client.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_refined(self):
        """Accepts devil's advocate output when long enough."""
        stage = ContextStage()
        mock_client = AsyncMock()
        original = "A" * 3000
        refined = "B" * 2500
        mock_client.generate = AsyncMock(return_value=refined)

        result = await stage._devil_advocate(
            mock_client, original, "Task", krag_context="## Code"
        )
        assert result == refined

    @pytest.mark.asyncio
    async def test_error_returns_original(self):
        """Client exception returns original unchanged."""
        stage = ContextStage()
        mock_client = AsyncMock()
        original = "Original reasoning"
        mock_client.generate = AsyncMock(
            side_effect=RuntimeError("GPU fault")
        )

        result = await stage._devil_advocate(
            mock_client, original, "Task", krag_context="## Code"
        )
        assert result == original


# ---------------------------------------------------------------------------
# Extract field group behavior
# ---------------------------------------------------------------------------

class TestExtractFieldGroupDeeper:
    """Deeper tests for _extract_field_group beyond test_stages.py."""

    @pytest.mark.asyncio
    async def test_truncated_json_repaired(self):
        """Truncated JSON from field extraction is repaired."""
        stage = ContextStage()
        mock_client = AsyncMock()
        # Truncated JSON — repair should close it
        mock_client.generate = AsyncMock(
            return_value='{"project_description": "Test", "key_requirements": ["R1"'
        )

        result = await stage._extract_field_group(
            mock_client, "reasoning",
            ["project_description", "key_requirements"],
            '{"project_description": "str", "key_requirements": []}',
            "description",
        )
        assert result["project_description"] == "Test"
        assert result["key_requirements"] == ["R1"]

    @pytest.mark.asyncio
    async def test_code_fenced_json(self):
        """JSON wrapped in code fences is extracted."""
        stage = ContextStage()
        mock_client = AsyncMock()
        mock_client.generate = AsyncMock(
            return_value='```json\n{"key": "value"}\n```'
        )

        result = await stage._extract_field_group(
            mock_client, "reasoning", ["key"], '{"key": "str"}', "test",
        )
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# Dependency cycle removal (roadmap_risk helper)
# ---------------------------------------------------------------------------

class TestRemoveDependencyCyclesDeeper:
    """Additional tests for _remove_dependency_cycles."""

    def test_string_phase_numbers_coerced(self):
        """'Phase 1' style numbers are coerced to int."""
        phases = [
            {"number": "Phase 1", "dependencies": []},
            {"number": "Phase 2", "dependencies": ["Phase 1"]},
        ]
        result = _remove_dependency_cycles(phases)
        assert result[0]["number"] == 1
        assert result[1]["number"] == 2
        assert result[1]["dependencies"] == [1]

    def test_complex_cycle(self):
        """3-way cycle: all back-edges removed."""
        phases = [
            {"number": 1, "dependencies": [3]},  # 3 > 1, invalid
            {"number": 2, "dependencies": [1]},   # valid
            {"number": 3, "dependencies": [2]},   # valid
        ]
        result = _remove_dependency_cycles(phases)
        assert result[0]["dependencies"] == []  # 3 removed
        assert result[1]["dependencies"] == [1]
        assert result[2]["dependencies"] == [2]

    def test_empty_phases(self):
        """Empty phase list returns empty."""
        assert _remove_dependency_cycles([]) == []
