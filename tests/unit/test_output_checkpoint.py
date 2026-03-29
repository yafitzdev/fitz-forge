# tests/unit/test_output_checkpoint.py
"""
Tests for PlanRenderer (output.py) and CheckpointManager (checkpoint.py).

PlanRenderer: converts PlanOutput to markdown.
CheckpointManager: save/load/clear pipeline checkpoints in SQLite.
"""

import json
from datetime import datetime, timezone

import aiosqlite
import pytest
import pytest_asyncio

from fitz_graveyard.planning.pipeline.checkpoint import CheckpointManager
from fitz_graveyard.planning.pipeline.output import PlanRenderer
from fitz_graveyard.planning.schemas.architecture import (
    Approach,
    ArchitectureOutput,
)
from fitz_graveyard.planning.schemas.context import Assumption, ContextOutput
from fitz_graveyard.planning.schemas.design import (
    ADR,
    Artifact,
    ComponentDesign,
    DesignOutput,
)
from fitz_graveyard.planning.schemas.plan_output import PlanOutput
from fitz_graveyard.planning.schemas.risk import Risk, RiskOutput
from fitz_graveyard.planning.schemas.roadmap import Phase, RoadmapOutput


# ---------------------------------------------------------------------------
# Helper: build a minimal PlanOutput
# ---------------------------------------------------------------------------


def _make_plan(
    *,
    job_description: str = "Build a widget",
    api_review_requested: bool = False,
    api_review_cost: dict | None = None,
    api_review_feedback: dict | None = None,
    diagnostics: dict | None = None,
    head_advanced: bool = False,
) -> PlanOutput:
    """Build a PlanOutput with sensible defaults for testing."""
    return PlanOutput(
        context=ContextOutput(
            project_description="A widget for things.",
            key_requirements=["Must be fast", "Must be secure"],
            constraints=["Python 3.12+"],
            stakeholders=["Engineering team"],
            existing_files=["src/widget.py"],
            needed_artifacts=["config.yaml"],
            assumptions=[
                Assumption(
                    assumption="REST not GraphQL",
                    impact="Architecture changes",
                    confidence="medium",
                ),
            ],
        ),
        architecture=ArchitectureOutput(
            approaches=[
                Approach(
                    name="Monolith",
                    description="Single service",
                    pros=["Simple"],
                    cons=["Scaling"],
                ),
                Approach(
                    name="Microservices",
                    description="Distributed",
                    pros=["Scalable"],
                    cons=["Complex"],
                ),
            ],
            recommended="Monolith",
            reasoning="Simplicity wins for this scope.",
            scope_statement="Small project, single developer.",
        ),
        design=DesignOutput(
            adrs=[
                ADR(
                    title="Use SQLite",
                    context="Need persistence",
                    decision="SQLite for local storage",
                    rationale="Simple, no server needed",
                    alternatives_considered=["PostgreSQL"],
                ),
            ],
            components=[
                ComponentDesign(
                    name="WidgetEngine",
                    purpose="Core logic",
                    responsibilities=["Parse input", "Generate output"],
                    interfaces=["process(data) -> Result"],
                ),
            ],
            data_model={"Widget": ["id: int", "name: str"]},
            artifacts=[
                Artifact(
                    filename="config.yaml",
                    content="key: value\nother: 42",
                    purpose="Configuration file",
                ),
            ],
        ),
        roadmap=RoadmapOutput(
            phases=[
                Phase(
                    number=1,
                    name="Foundation",
                    objective="Set up project skeleton",
                    deliverables=["pyproject.toml", "src/__init__.py"],
                    estimated_effort="~2 hours",
                    dependencies=[],
                    verification_command="pytest --co -q",
                ),
                Phase(
                    number=2,
                    name="Core Logic",
                    objective="Implement widget engine",
                    deliverables=["src/engine.py"],
                    dependencies=[1],
                ),
            ],
            critical_path=[1, 2],
        ),
        risk=RiskOutput(
            risks=[
                Risk(
                    category="technical",
                    description="SQLite may hit concurrency limits",
                    impact="high",
                    likelihood="low",
                    mitigation="Use WAL mode",
                    contingency="Migrate to PostgreSQL",
                    verification="pytest tests/unit/test_concurrency.py",
                    affected_phases=[2],
                ),
            ],
        ),
        job_description=job_description,
        git_sha="abc123",
        generated_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        api_review_requested=api_review_requested,
        api_review_cost=api_review_cost,
        api_review_feedback=api_review_feedback,
        diagnostics=diagnostics or {},
    )


# ===========================================================================
# PlanRenderer tests
# ===========================================================================


class TestPlanRenderer:
    """Tests for PlanRenderer.render()."""

    def test_render_contains_frontmatter(self):
        """Output starts with YAML frontmatter delimiters."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert md.startswith("---\n")
        assert "generated_at:" in md
        assert 'git_sha: "abc123"' in md

    def test_render_contains_title(self):
        """Output contains job description as title."""
        renderer = PlanRenderer()
        plan = _make_plan(job_description="Build a REST API")
        md = renderer.render(plan)

        assert "# Build a REST API" in md

    def test_render_context_section(self):
        """Context section includes description, requirements, constraints."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "## Context" in md
        assert "A widget for things." in md
        assert "- Must be fast" in md
        assert "- Must be secure" in md
        assert "- Python 3.12+" in md
        assert "- Engineering team" in md

    def test_render_existing_files(self):
        """Existing files listed in context."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "**Existing Files:**" in md
        assert "- src/widget.py" in md

    def test_render_needed_artifacts(self):
        """Needed artifacts listed in context."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "**Expected Deliverables:**" in md
        assert "- config.yaml" in md

    def test_render_assumptions(self):
        """Assumptions rendered with confidence and impact."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "**Assumptions (verify these):**" in md
        assert "REST not GraphQL" in md
        assert "[medium]" in md
        assert "Architecture changes" in md

    def test_render_architecture_section(self):
        """Architecture section includes approaches and recommendation."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "## Architecture" in md
        assert "### Explored Approaches" in md
        assert "Monolith" in md
        assert "Microservices" in md
        assert "### Recommended: Monolith" in md
        assert "Simplicity wins" in md
        assert "Small project" in md

    def test_render_design_section(self):
        """Design section includes ADRs, components, data model, artifacts."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "## Design" in md
        assert "### Architectural Decision Records" in md
        assert "Use SQLite" in md
        assert "### Components" in md
        assert "WidgetEngine" in md
        assert "### Data Model" in md
        assert "Widget" in md
        assert "### Artifacts" in md
        assert "config.yaml" in md
        assert "```yaml" in md
        assert "key: value" in md

    def test_render_roadmap_section(self):
        """Roadmap section includes phases with details."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "## Roadmap" in md
        assert "### Phase 1: Foundation" in md
        assert "**Objective:** Set up project skeleton" in md
        assert "- pyproject.toml" in md
        assert "**Effort:** ~2 hours" in md
        assert "### Phase 2: Core Logic" in md
        assert "**Dependencies:** Phases 1" in md
        assert "### Critical Path" in md

    def test_render_risk_section(self):
        """Risk section includes risk details."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan)

        assert "## Risk Analysis" in md
        assert "### Technical Risk" in md
        assert "SQLite may hit concurrency limits" in md
        assert "**Impact:** high" in md
        assert "WAL mode" in md
        assert "**Contingency:** Migrate to PostgreSQL" in md
        assert "**Affected Phases:** 2" in md

    def test_render_head_advanced_warning(self):
        """head_advanced=True inserts WARNING blockquote."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan, head_advanced=True)

        assert "WARNING" in md
        assert "HEAD advanced" in md

    def test_render_no_warning_by_default(self):
        """head_advanced=False (default) has no warning."""
        renderer = PlanRenderer()
        plan = _make_plan()
        md = renderer.render(plan, head_advanced=False)

        assert "HEAD advanced" not in md

    def test_render_api_review_performed(self):
        """API review with results shows cost and feedback."""
        renderer = PlanRenderer()
        plan = _make_plan(
            api_review_requested=True,
            api_review_cost={
                "sections_reviewed": 2,
                "actual_input_tokens": 1000,
                "actual_output_tokens": 500,
                "actual_cost_usd": 0.0123,
                "actual_cost_eur": 0.0115,
                "estimate": {"model": "claude-sonnet"},
            },
            api_review_feedback={
                "architecture": "Looks good, consider caching.",
                "design": "Add error handling to component X.",
            },
        )
        md = renderer.render(plan)

        assert "## API Review" in md
        assert "### Cost Summary" in md
        assert "Sections reviewed: 2" in md
        assert "$0.0123 USD" in md
        assert "claude-sonnet" in md
        assert "### Section Feedback" in md
        assert "Looks good, consider caching." in md

    def test_render_api_review_no_sections_flagged(self):
        """API review requested but no sections flagged."""
        renderer = PlanRenderer()
        plan = _make_plan(
            api_review_requested=True,
            api_review_cost={"sections_reviewed": 0},
        )
        md = renderer.render(plan)

        assert "All sections above confidence threshold" in md

    def test_render_api_review_not_requested(self):
        """No API review section when not requested."""
        renderer = PlanRenderer()
        plan = _make_plan(api_review_requested=False)
        md = renderer.render(plan)

        assert "## API Review" not in md

    def test_render_diagnostics(self):
        """Diagnostics section renders as table."""
        renderer = PlanRenderer()
        plan = _make_plan(
            diagnostics={
                "provider": "llama_cpp",
                "model": "qwen3-coder-30b",
                "quant": "Q6",
                "context_length": 65536,
                "agent_enabled": True,
                "total_llm_calls": 15,
                "total_generation_s": 120.5,
                "stage_timings_s": {"context": 30.0, "architecture_design": 60.0, "roadmap_risk": 30.5},
            },
        )
        md = renderer.render(plan)

        assert "## Diagnostics" in md
        assert "| Provider | llama_cpp |" in md
        assert "| Model | qwen3-coder-30b |" in md
        assert "| Quantization | Q6 |" in md
        assert "| Context window | 65,536 tokens |" in md
        assert "| Agent | enabled |" in md
        assert "| Total LLM calls | 15 |" in md

    def test_render_diagnostics_agent_files(self):
        """Agent file selection diagnostics render correctly."""
        renderer = PlanRenderer()
        plan = _make_plan(
            diagnostics={
                "agent_files": {
                    "total_screened": 50,
                    "scan_hits": ["src/a.py", "src/b.py"],
                    "selected": ["src/a.py", "src/b.py", "src/c.py"],
                    "included": ["src/a.py", "src/b.py"],
                },
            },
        )
        md = renderer.render(plan)

        assert "### Agent File Selection" in md
        assert "**Screened**: 50 files" in md
        assert "**Structural scan** (2 hits)" in md

    def test_render_frontmatter_api_review_cost(self):
        """Frontmatter includes API review cost when present."""
        renderer = PlanRenderer()
        plan = _make_plan(
            api_review_requested=True,
            api_review_cost={"actual_cost_usd": 0.05, "actual_cost_eur": 0.046},
        )
        md = renderer.render(plan)

        assert "api_review_requested: true" in md
        assert "api_review_cost_usd: 0.0500" in md


# ===========================================================================
# CheckpointManager tests
# ===========================================================================


class TestCheckpointManager:
    """Tests for CheckpointManager save/load/clear."""

    @pytest_asyncio.fixture
    async def checkpoint_env(self, tmp_path):
        """Set up SQLite DB with jobs table for checkpoint tests."""
        db_path = str(tmp_path / "checkpoint_test.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    pipeline_state TEXT
                )
                """
            )
            await db.execute(
                "INSERT INTO jobs (id, pipeline_state) VALUES (?, ?)",
                ("job-001", None),
            )
            await db.commit()
        yield db_path

    @pytest.mark.asyncio
    async def test_save_and_load(self, checkpoint_env):
        """Save a stage and load it back."""
        mgr = CheckpointManager(checkpoint_env)

        await mgr.save_stage("job-001", "context", {"project_description": "A widget"})
        result = await mgr.load_checkpoint("job-001")

        assert "context" in result
        assert result["context"]["project_description"] == "A widget"

    @pytest.mark.asyncio
    async def test_save_multiple_stages(self, checkpoint_env):
        """Multiple stages accumulate in the checkpoint."""
        mgr = CheckpointManager(checkpoint_env)

        await mgr.save_stage("job-001", "context", {"desc": "first"})
        await mgr.save_stage("job-001", "architecture", {"approach": "monolith"})

        result = await mgr.load_checkpoint("job-001")
        assert "context" in result
        assert "architecture" in result
        assert result["context"]["desc"] == "first"
        assert result["architecture"]["approach"] == "monolith"

    @pytest.mark.asyncio
    async def test_save_overwrites_stage(self, checkpoint_env):
        """Re-saving the same stage overwrites it."""
        mgr = CheckpointManager(checkpoint_env)

        await mgr.save_stage("job-001", "context", {"version": 1})
        await mgr.save_stage("job-001", "context", {"version": 2})

        result = await mgr.load_checkpoint("job-001")
        assert result["context"]["version"] == 2

    @pytest.mark.asyncio
    async def test_load_empty_checkpoint(self, checkpoint_env):
        """Loading job with no checkpoint returns empty dict."""
        mgr = CheckpointManager(checkpoint_env)
        result = await mgr.load_checkpoint("job-001")
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_nonexistent_job_raises(self, checkpoint_env):
        """Loading nonexistent job raises ValueError."""
        mgr = CheckpointManager(checkpoint_env)

        with pytest.raises(ValueError, match="not found"):
            await mgr.load_checkpoint("nonexistent")

    @pytest.mark.asyncio
    async def test_save_nonexistent_job_raises(self, checkpoint_env):
        """Saving to nonexistent job raises ValueError."""
        mgr = CheckpointManager(checkpoint_env)

        with pytest.raises(ValueError, match="not found"):
            await mgr.save_stage("nonexistent", "context", {"x": 1})

    @pytest.mark.asyncio
    async def test_clear_checkpoint(self, checkpoint_env):
        """Clearing checkpoint removes all stage data."""
        mgr = CheckpointManager(checkpoint_env)

        await mgr.save_stage("job-001", "context", {"x": 1})
        await mgr.clear_checkpoint("job-001")

        result = await mgr.load_checkpoint("job-001")
        assert result == {}

    @pytest.mark.asyncio
    async def test_clear_nonexistent_job_raises(self, checkpoint_env):
        """Clearing nonexistent job raises ValueError."""
        mgr = CheckpointManager(checkpoint_env)

        with pytest.raises(ValueError, match="not found"):
            await mgr.clear_checkpoint("nonexistent")

    @pytest.mark.asyncio
    async def test_load_unwraps_timestamped_format(self, checkpoint_env):
        """Loading checkpoint unwraps timestamped format (output + completed_at)."""
        mgr = CheckpointManager(checkpoint_env)

        # save_stage writes timestamped format
        await mgr.save_stage("job-001", "context", {"key": "value"})
        result = await mgr.load_checkpoint("job-001")

        # Should be unwrapped
        assert result["context"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_load_handles_old_format(self, checkpoint_env):
        """Loading checkpoint handles old non-timestamped format."""
        # Write old format directly
        async with aiosqlite.connect(checkpoint_env) as db:
            old_state = json.dumps({"context": {"key": "old_value"}})
            await db.execute(
                "UPDATE jobs SET pipeline_state = ? WHERE id = ?",
                (old_state, "job-001"),
            )
            await db.commit()

        mgr = CheckpointManager(checkpoint_env)
        result = await mgr.load_checkpoint("job-001")

        # Old format passed through as-is
        assert result["context"] == {"key": "old_value"}
