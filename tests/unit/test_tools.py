# tests/unit/test_tools.py
"""
Tests for tools/ service layer functions.

Covers create_plan, get_plan, list_plans, check_status, retry_job, replay_plan.
Uses InMemoryJobStore for fast isolation; SQLite integration in test_tools_integration.py.
"""

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from fastmcp.exceptions import ToolError

from fitz_forge.config.schema import FitzPlannerConfig
from fitz_forge.models.jobs import (
    InMemoryJobStore,
    JobRecord,
    JobState,
    generate_job_id,
)
from fitz_forge.tools.check_status import check_status
from fitz_forge.tools.create_plan import create_plan
from fitz_forge.tools.get_plan import get_plan
from fitz_forge.tools.list_plans import list_plans
from fitz_forge.tools.retry_job import retry_job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store():
    """In-memory job store for testing."""
    return InMemoryJobStore()


@pytest.fixture
def config():
    """Minimal FitzPlannerConfig with defaults."""
    return FitzPlannerConfig()


# ---------------------------------------------------------------------------
# create_plan
# ---------------------------------------------------------------------------


class TestCreatePlan:
    """Tests for create_plan tool."""

    @pytest.mark.asyncio
    async def test_happy_path(self, store, config):
        """Valid inputs produce a queued job and response dict."""
        result = await create_plan(
            description="Build a REST API",
            timeline="2 weeks",
            context="Greenfield project",
            integration_points=["PostgreSQL"],
            api_review=False,
            store=store,
            config=config,
        )

        assert result["status"] == "queued"
        assert "job_id" in result
        assert result["api_review"] is False
        assert "check_status" in result["next_steps"]

        # Verify persisted
        record = await store.get(result["job_id"])
        assert record is not None
        assert record.description == "Build a REST API"
        assert record.timeline == "2 weeks"
        assert record.context == "Greenfield project"
        assert record.integration_points == ["PostgreSQL"]
        assert record.state == JobState.QUEUED

    @pytest.mark.asyncio
    async def test_api_review_enabled(self, store, config):
        """api_review=True sets flag and adjusts next_steps."""
        result = await create_plan(
            description="Build something with review",
            timeline=None,
            context=None,
            integration_points=None,
            api_review=True,
            store=store,
            config=config,
        )

        assert result["api_review"] is True
        assert "confirm_review" in result["next_steps"]
        assert "cancel_review" in result["next_steps"]

    @pytest.mark.asyncio
    async def test_optional_fields_none(self, store, config):
        """None optional fields are handled without error."""
        result = await create_plan(
            description="Minimal job",
            timeline=None,
            context=None,
            integration_points=None,
            api_review=False,
            store=store,
            config=config,
        )

        record = await store.get(result["job_id"])
        assert record.timeline is None
        assert record.context is None
        assert record.integration_points == []

    @pytest.mark.asyncio
    async def test_empty_description_raises(self, store, config):
        """Empty description raises ToolError."""
        with pytest.raises(ToolError, match="(?i)empty"):
            await create_plan(
                description="   ",
                timeline=None,
                context=None,
                integration_points=None,
                api_review=False,
                store=store,
                config=config,
            )

    @pytest.mark.asyncio
    async def test_description_stripped(self, store, config):
        """Leading/trailing whitespace stripped from description."""
        result = await create_plan(
            description="  Build a thing  ",
            timeline="  1 week  ",
            context="  Some context  ",
            integration_points=None,
            api_review=False,
            store=store,
            config=config,
        )

        record = await store.get(result["job_id"])
        assert record.description == "Build a thing"
        assert record.timeline == "1 week"
        assert record.context == "Some context"

    @pytest.mark.asyncio
    async def test_source_dir_valid(self, store, config, tmp_path):
        """Valid source_dir is accepted and stored."""
        result = await create_plan(
            description="With source dir",
            timeline=None,
            context=None,
            integration_points=None,
            api_review=False,
            store=store,
            config=config,
            source_dir=str(tmp_path),
        )

        record = await store.get(result["job_id"])
        assert record.source_dir is not None

    @pytest.mark.asyncio
    async def test_source_dir_nonexistent_raises(self, store, config):
        """Nonexistent source_dir raises ToolError."""
        with pytest.raises(ToolError, match="(?i)does not exist"):
            await create_plan(
                description="Bad source dir",
                timeline=None,
                context=None,
                integration_points=None,
                api_review=False,
                store=store,
                config=config,
                source_dir="/nonexistent/path/abc123",
            )

    @pytest.mark.asyncio
    async def test_response_contains_model(self, store, config):
        """Response includes model name from config."""
        result = await create_plan(
            description="Check model field",
            timeline=None,
            context=None,
            integration_points=None,
            api_review=False,
            store=store,
            config=config,
        )

        assert "model" in result
        assert result["model"] == config.ollama.model


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    """Tests for check_status tool."""

    @pytest.mark.asyncio
    async def test_queued_job(self, store, make_job):
        """Queued job returns correct state and message."""
        job = make_job(state=JobState.QUEUED)
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["job_id"] == job.job_id
        assert result["state"] == "queued"
        assert result["progress"] == 0.0
        assert "queued" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_running_job_with_phase(self, store, make_job):
        """Running job includes phase and progress in message."""
        job = make_job(
            state=JobState.RUNNING,
            progress=0.5,
            current_phase="architecture",
        )
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["state"] == "running"
        assert result["progress"] == 0.5
        assert "architecture" in result["message"]
        assert "50.0%" in result["message"]

    @pytest.mark.asyncio
    async def test_complete_job(self, store, make_job):
        """Complete job message includes quality score."""
        job = make_job(state=JobState.COMPLETE, quality_score=0.85)
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["state"] == "complete"
        assert "0.85" in result["message"]

    @pytest.mark.asyncio
    async def test_failed_job(self, store, make_job):
        """Failed job message includes error and retry suggestion."""
        job = make_job(state=JobState.FAILED, error="OOM killed")
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["state"] == "failed"
        assert "OOM killed" in result["message"]
        assert "retry_job" in result["message"]
        assert result["error"] == "OOM killed"

    @pytest.mark.asyncio
    async def test_interrupted_job(self, store, make_job):
        """Interrupted job message suggests retry."""
        job = make_job(state=JobState.INTERRUPTED, error="Server restart")
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["state"] == "interrupted"
        assert "interrupted" in result["message"].lower()
        assert "retry_job" in result["message"]

    @pytest.mark.asyncio
    async def test_awaiting_review_with_cost(self, store, make_job):
        """Awaiting review job includes cost estimate."""
        job = make_job(
            state=JobState.AWAITING_REVIEW,
            api_review=True,
            cost_estimate_json='{"total_cost_usd": 0.15}',
        )
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["state"] == "awaiting_review"
        assert "confirm_review" in result["message"]
        assert "cancel_review" in result["message"]
        assert "$0.1500" in result["message"]
        assert result["cost_estimate"]["total_cost_usd"] == 0.15

    @pytest.mark.asyncio
    async def test_awaiting_review_without_cost(self, store, make_job):
        """Awaiting review without cost estimate still works."""
        job = make_job(state=JobState.AWAITING_REVIEW, api_review=True)
        await store.add(job)

        result = await check_status(job.job_id, store=store)

        assert result["state"] == "awaiting_review"
        assert "not yet available" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_not_found(self, store):
        """Nonexistent job raises ToolError."""
        with pytest.raises(ToolError, match="(?i)not found"):
            await check_status("abcdef123456", store=store)

    @pytest.mark.asyncio
    async def test_invalid_job_id(self, store):
        """Invalid job ID format raises ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            await check_status("x", store=store)


# ---------------------------------------------------------------------------
# get_plan
# ---------------------------------------------------------------------------


class TestGetPlan:
    """Tests for get_plan tool."""

    @pytest.mark.asyncio
    async def test_full_format(self, store, make_job, tmp_path):
        """Full format returns entire plan content."""
        plan_file = tmp_path / "plan.md"
        plan_content = (
            "# My Plan\n\n"
            "## Context\nSome context.\n\n"
            "## Architecture\nSome arch.\n\n"
            "## Design\nSome design.\n\n"
            "## Roadmap\nSome roadmap.\n\n"
            "## Risk\nSome risk.\n"
        )
        plan_file.write_text(plan_content, encoding="utf-8")

        job = make_job(
            state=JobState.COMPLETE,
            file_path=str(plan_file),
            quality_score=0.9,
        )
        await store.add(job)

        result = await get_plan(job.job_id, format="full", store=store)

        assert result["job_id"] == job.job_id
        assert result["format"] == "full"
        assert result["content"] == plan_content
        assert result["quality_score"] == 0.9
        assert result["file_path"] == str(plan_file)

    @pytest.mark.asyncio
    async def test_summary_format(self, store, make_job, tmp_path):
        """Summary format excludes Roadmap and Risk sections."""
        plan_file = tmp_path / "plan.md"
        plan_content = (
            "# My Plan\n\n"
            "## Context\nSome context.\n\n"
            "## Roadmap\nSome roadmap.\n\n"
            "## Risk\nSome risk.\n"
        )
        plan_file.write_text(plan_content, encoding="utf-8")

        job = make_job(state=JobState.COMPLETE, file_path=str(plan_file))
        await store.add(job)

        result = await get_plan(job.job_id, format="summary", store=store)

        assert "## Context" in result["content"]
        assert "## Roadmap" not in result["content"]
        assert "## Risk" not in result["content"]

    @pytest.mark.asyncio
    async def test_roadmap_only_format(self, store, make_job, tmp_path):
        """Roadmap-only format returns only roadmap section."""
        plan_file = tmp_path / "plan.md"
        plan_content = (
            "# My Plan\n\n"
            "## Context\nSome context.\n\n"
            "## Roadmap\nPhase 1: Build\nPhase 2: Test\n"
        )
        plan_file.write_text(plan_content, encoding="utf-8")

        job = make_job(state=JobState.COMPLETE, file_path=str(plan_file))
        await store.add(job)

        result = await get_plan(job.job_id, format="roadmap_only", store=store)

        assert "## Roadmap" in result["content"]
        assert "Phase 1" in result["content"]
        assert "## Context" not in result["content"]

    @pytest.mark.asyncio
    async def test_invalid_format_raises(self, store, make_job):
        """Invalid format raises ToolError."""
        job = make_job(state=JobState.COMPLETE)
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)invalid format"):
            await get_plan(job.job_id, format="xml", store=store)

    @pytest.mark.asyncio
    async def test_not_complete_raises(self, store, make_job):
        """Requesting plan for non-complete job raises ToolError."""
        job = make_job(state=JobState.RUNNING)
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)running"):
            await get_plan(job.job_id, format="full", store=store)

    @pytest.mark.asyncio
    async def test_no_file_path_raises(self, store, make_job):
        """Complete job with no file_path raises ToolError."""
        job = make_job(state=JobState.COMPLETE, file_path=None)
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)no plan file"):
            await get_plan(job.job_id, format="full", store=store)

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, store, make_job):
        """Complete job with missing file raises ToolError."""
        job = make_job(
            state=JobState.COMPLETE,
            file_path="/nonexistent/plan.md",
        )
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)could not read"):
            await get_plan(job.job_id, format="full", store=store)

    @pytest.mark.asyncio
    async def test_not_found(self, store):
        """Nonexistent job raises ToolError."""
        with pytest.raises(ToolError, match="(?i)not found"):
            await get_plan("abcdef123456", format="full", store=store)

    @pytest.mark.asyncio
    async def test_invalid_job_id(self, store):
        """Invalid job ID format raises ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            await get_plan("x", format="full", store=store)


# ---------------------------------------------------------------------------
# list_plans
# ---------------------------------------------------------------------------


class TestListPlans:
    """Tests for list_plans tool."""

    @pytest.mark.asyncio
    async def test_empty_store(self, store):
        """Empty store returns zero plans."""
        result = await list_plans(store=store)

        assert result["total"] == 0
        assert result["plans"] == []

    @pytest.mark.asyncio
    async def test_multiple_plans(self, store, make_job):
        """Multiple jobs all appear in response."""
        for i in range(3):
            await store.add(make_job(description=f"Job {i}"))

        result = await list_plans(store=store)

        assert result["total"] == 3
        assert len(result["plans"]) == 3

    @pytest.mark.asyncio
    async def test_description_truncated(self, store, make_job):
        """Long descriptions are truncated to 80 chars."""
        long_desc = "A" * 200
        await store.add(make_job(description=long_desc))

        result = await list_plans(store=store)

        plan = result["plans"][0]
        assert len(plan["description"]) <= 80
        assert plan["description"].endswith("...")

    @pytest.mark.asyncio
    async def test_plan_summary_fields(self, store, make_job):
        """Each plan summary contains expected fields."""
        job = make_job(
            state=JobState.COMPLETE,
            quality_score=0.75,
            file_path="/some/plan.md",
        )
        await store.add(job)

        result = await list_plans(store=store)
        plan = result["plans"][0]

        assert plan["job_id"] == job.job_id
        assert plan["state"] == "complete"
        assert plan["quality_score"] == 0.75
        assert plan["file_path"] == "/some/plan.md"
        assert "created_at" in plan


# ---------------------------------------------------------------------------
# retry_job
# ---------------------------------------------------------------------------


class TestRetryJob:
    """Tests for retry_job tool."""

    @pytest.mark.asyncio
    async def test_retry_failed(self, store, make_job):
        """Failed job is re-queued successfully."""
        job = make_job(
            state=JobState.FAILED,
            progress=0.5,
            error="Connection timeout",
            current_phase="phase2",
        )
        await store.add(job)

        result = await retry_job(job.job_id, store=store)

        assert result["job_id"] == job.job_id
        assert result["status"] == "re-queued"

        updated = await store.get(job.job_id)
        assert updated.state == JobState.QUEUED
        assert updated.progress == 0.0
        assert updated.error is None
        assert updated.current_phase is None

    @pytest.mark.asyncio
    async def test_retry_interrupted(self, store, make_job):
        """Interrupted job is re-queued successfully."""
        job = make_job(
            state=JobState.INTERRUPTED,
            error="Server shutdown",
        )
        await store.add(job)

        result = await retry_job(job.job_id, store=store)

        assert result["status"] == "re-queued"
        updated = await store.get(job.job_id)
        assert updated.state == JobState.QUEUED

    @pytest.mark.asyncio
    async def test_retry_running_raises(self, store, make_job):
        """Retrying a running job raises ToolError."""
        job = make_job(state=JobState.RUNNING)
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)running"):
            await retry_job(job.job_id, store=store)

    @pytest.mark.asyncio
    async def test_retry_queued_raises(self, store, make_job):
        """Retrying a queued job raises ToolError."""
        job = make_job(state=JobState.QUEUED)
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)queued"):
            await retry_job(job.job_id, store=store)

    @pytest.mark.asyncio
    async def test_retry_complete_raises(self, store, make_job):
        """Retrying a complete job raises ToolError."""
        job = make_job(state=JobState.COMPLETE)
        await store.add(job)

        with pytest.raises(ToolError, match="(?i)complete"):
            await retry_job(job.job_id, store=store)

    @pytest.mark.asyncio
    async def test_retry_not_found(self, store):
        """Nonexistent job raises ToolError."""
        with pytest.raises(ToolError, match="(?i)not found"):
            await retry_job("abcdef123456", store=store)

    @pytest.mark.asyncio
    async def test_retry_invalid_job_id(self, store):
        """Invalid job ID format raises ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            await retry_job("x", store=store)


# ---------------------------------------------------------------------------
# replay_plan (uses aiosqlite, needs tmp_path)
# ---------------------------------------------------------------------------


class TestReplayPlan:
    """Tests for replay_plan tool."""

    @pytest_asyncio.fixture
    async def sqlite_env(self, tmp_path):
        """Set up SQLite store and db_path for replay tests."""
        import aiosqlite
        from fitz_forge.models.sqlite_store import SQLiteJobStore

        db_path = str(tmp_path / "replay_test.db")
        store = SQLiteJobStore(db_path)
        await store.initialize()
        yield store, db_path
        await store.close()

    @pytest.mark.asyncio
    async def test_replay_happy_path(self, sqlite_env):
        """Replay copies agent context into a new job."""
        from fitz_forge.tools.replay_plan import replay_plan

        store, db_path = sqlite_env
        import aiosqlite

        # Create source job
        source_id = generate_job_id()
        source = JobRecord(
            job_id=source_id,
            description="Original job",
            timeline="1 week",
            context="Some context",
            integration_points=["api1"],
            state=JobState.COMPLETE,
            progress=1.0,
            current_phase=None,
            quality_score=0.8,
            created_at=datetime.now(timezone.utc),
            api_review=False,
        )
        await store.add(source)

        # Write checkpoint with agent context
        checkpoint = {
            "_agent_context": {
                "output": {
                    "synthesized": "# Codebase overview",
                    "raw_summaries": "## File summaries",
                },
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE jobs SET pipeline_state = ? WHERE id = ?",
                (json.dumps(checkpoint), source_id),
            )
            await db.commit()

        result = await replay_plan(source_id, store=store, db_path=db_path)

        assert "job_id" in result
        assert result["source_job_id"] == source_id
        assert result["description"] == "Original job"

        # Verify new job exists
        new_job = await store.get(result["job_id"])
        assert new_job is not None
        assert new_job.state == JobState.QUEUED
        assert new_job.description == "Original job"

    @pytest.mark.asyncio
    async def test_replay_not_found(self, sqlite_env):
        """Replaying nonexistent job raises ValueError."""
        from fitz_forge.tools.replay_plan import replay_plan

        store, db_path = sqlite_env

        with pytest.raises(ValueError, match="(?i)not found"):
            await replay_plan("nonexistent99", store=store, db_path=db_path)

    @pytest.mark.asyncio
    async def test_replay_no_checkpoint(self, sqlite_env):
        """Replaying job without checkpoint raises ValueError."""
        from fitz_forge.tools.replay_plan import replay_plan

        store, db_path = sqlite_env

        job_id = generate_job_id()
        job = JobRecord(
            job_id=job_id,
            description="No checkpoint",
            timeline=None,
            context=None,
            integration_points=[],
            state=JobState.COMPLETE,
            progress=1.0,
            current_phase=None,
            quality_score=0.5,
            created_at=datetime.now(timezone.utc),
        )
        await store.add(job)

        with pytest.raises(ValueError, match="(?i)no checkpoint"):
            await replay_plan(job_id, store=store, db_path=db_path)

    @pytest.mark.asyncio
    async def test_replay_no_agent_context(self, sqlite_env):
        """Replaying job with checkpoint but no agent context raises ValueError."""
        from fitz_forge.tools.replay_plan import replay_plan
        import aiosqlite

        store, db_path = sqlite_env

        job_id = generate_job_id()
        job = JobRecord(
            job_id=job_id,
            description="Has checkpoint, no agent",
            timeline=None,
            context=None,
            integration_points=[],
            state=JobState.COMPLETE,
            progress=1.0,
            current_phase=None,
            quality_score=0.6,
            created_at=datetime.now(timezone.utc),
        )
        await store.add(job)

        # Write checkpoint without _agent_context
        checkpoint = {"context_stage": {"output": {"some": "data"}}}
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE jobs SET pipeline_state = ? WHERE id = ?",
                (json.dumps(checkpoint), job_id),
            )
            await db.commit()

        with pytest.raises(ValueError, match="(?i)no agent context"):
            await replay_plan(job_id, store=store, db_path=db_path)
