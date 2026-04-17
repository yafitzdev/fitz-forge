# tests/unit/test_cli.py
"""
CLI unit tests.

Tests each command via typer's CliRunner, using an in-memory job store
to avoid filesystem side effects.
"""

import pytest
from typer.testing import CliRunner
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from fitz_forge.cli import app
from fitz_forge.models.jobs import JobRecord, JobState

runner = CliRunner()

# 12-char hex IDs (matches generate_job_id format)
JOB_ID_1 = "abc123def456"
JOB_ID_2 = "789012abcdef"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_job(
    job_id=JOB_ID_1,
    description="build auth system",
    state=JobState.QUEUED,
    progress=0.0,
    quality_score=None,
    file_path=None,
    error=None,
    api_review=False,
    cost_estimate_json=None,
):
    return JobRecord(
        job_id=job_id,
        description=description,
        timeline=None,
        context=None,
        integration_points=[],
        state=state,
        progress=progress,
        current_phase=None,
        quality_score=quality_score,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        file_path=file_path,
        error=error,
        api_review=api_review,
        cost_estimate_json=cost_estimate_json,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHelp:
    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        assert "Local-first AI architectural planning" in result.output

    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "plan" in result.output
        assert "run" in result.output
        assert "list" in result.output
        assert "serve" in result.output


class TestPlan:
    @patch("fitz_forge.cli._get_store")
    @patch("fitz_forge.cli.load_config", create=True)
    def test_plan_queues_job(self, mock_config, mock_store):
        """plan --detach queues the job and prints job ID without running inline."""
        mock_store_instance = AsyncMock()
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        with patch("fitz_forge.tools.create_plan.sanitize_description", return_value="test desc"):
            with patch("fitz_forge.tools.create_plan.generate_job_id", return_value="test12345678"):
                mock_store_instance.add = AsyncMock()
                result = runner.invoke(app, ["plan", "test desc", "--detach"])

        assert result.exit_code == 0
        assert "test12345678" in result.output
        assert "fitz-forge run" in result.output


class TestList:
    @patch("fitz_forge.cli._get_store")
    def test_list_empty(self, mock_store):
        """list shows 'No plans' when empty."""
        mock_store_instance = AsyncMock()
        mock_store_instance.list_all = AsyncMock(return_value=[])
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No plans" in result.output

    @patch("fitz_forge.cli._get_store")
    def test_list_with_jobs(self, mock_store):
        """list shows jobs in table format."""
        jobs = [
            _make_job(JOB_ID_1, "build auth", JobState.COMPLETE, 1.0, 0.82),
            _make_job(JOB_ID_2, "add caching", JobState.QUEUED),
        ]
        mock_store_instance = AsyncMock()
        mock_store_instance.list_all = AsyncMock(return_value=jobs)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert JOB_ID_1 in result.output
        assert JOB_ID_2 in result.output
        assert "complete" in result.output
        assert "queued" in result.output


class TestStatus:
    @patch("fitz_forge.cli._get_store")
    def test_status_queued(self, mock_store):
        """status shows job details."""
        job = _make_job()
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["status", JOB_ID_1])
        assert result.exit_code == 0
        assert JOB_ID_1 in result.output
        assert "queued" in result.output
        assert "0%" in result.output

    @patch("fitz_forge.cli._get_store")
    def test_status_not_found(self, mock_store):
        """status exits with error for unknown job."""
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=None)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["status", "nonexistent12"])
        assert result.exit_code == 1


class TestGet:
    @patch("fitz_forge.cli._get_store")
    @patch("builtins.open", new_callable=MagicMock)
    def test_get_complete_job(self, mock_open, mock_store):
        """get prints plan markdown."""
        plan_content = "# Plan for: build auth system\n\nSome plan content."
        mock_open.return_value.__enter__.return_value.read.return_value = plan_content

        job = _make_job(state=JobState.COMPLETE, quality_score=0.82, file_path="/tmp/plan.md")
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["get", JOB_ID_1])
        assert result.exit_code == 0
        assert "build auth system" in result.output

    @patch("fitz_forge.cli._get_store")
    def test_get_incomplete_job_errors(self, mock_store):
        """get fails for non-complete jobs."""
        job = _make_job(state=JobState.RUNNING)
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["get", JOB_ID_1])
        assert result.exit_code == 1


class TestRetry:
    @patch("fitz_forge.cli._get_store")
    def test_retry_failed_job(self, mock_store):
        """retry re-queues a failed job."""
        job = _make_job(state=JobState.FAILED, error="OOM")
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.update = AsyncMock()
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["retry", JOB_ID_1])
        assert result.exit_code == 0
        assert "re-queued" in result.output

    @patch("fitz_forge.cli._get_store")
    def test_retry_queued_job_errors(self, mock_store):
        """retry fails for non-retryable states."""
        job = _make_job(state=JobState.QUEUED)
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["retry", JOB_ID_1])
        assert result.exit_code == 1


class TestConfirm:
    @patch("fitz_forge.cli._get_store")
    def test_confirm_awaiting_review(self, mock_store):
        """confirm approves API review."""
        job = _make_job(state=JobState.AWAITING_REVIEW, api_review=True)
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.update = AsyncMock()
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["confirm", JOB_ID_1])
        assert result.exit_code == 0
        assert "approved" in result.output

    @patch("fitz_forge.cli._get_store")
    def test_confirm_wrong_state_errors(self, mock_store):
        """confirm fails if job not awaiting review."""
        job = _make_job(state=JobState.QUEUED)
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["confirm", JOB_ID_1])
        assert result.exit_code == 1


class TestCancel:
    @patch("fitz_forge.cli._get_store")
    def test_cancel_awaiting_review(self, mock_store):
        """cancel skips API review."""
        job = _make_job(state=JobState.AWAITING_REVIEW, api_review=True)
        mock_store_instance = AsyncMock()
        mock_store_instance.get = AsyncMock(return_value=job)
        mock_store_instance.update = AsyncMock()
        mock_store_instance.close = AsyncMock()
        mock_store.return_value = mock_store_instance

        result = runner.invoke(app, ["cancel", JOB_ID_1])
        assert result.exit_code == 0
        assert "skipped" in result.output


# ---------------------------------------------------------------------------
# Enhanced progress display tests
# ---------------------------------------------------------------------------


class TestDescribePhase:
    """Tests for fitz_forge.models.events.describe_phase."""

    def test_known_phase_direct_lookup(self):
        from fitz_forge.models.events import describe_phase

        assert describe_phase("health_check") == "Checking LLM connectivity..."

    def test_reasoning_substep(self):
        from fitz_forge.models.events import describe_phase

        assert (
            describe_phase("architecture_design:reasoning")
            == "Exploring architecture and design..."
        )

    def test_critiquing_substep(self):
        from fitz_forge.models.events import describe_phase

        assert (
            describe_phase("architecture_design:critiquing")
            == "Reviewing analysis for quality..."
        )

    def test_agent_mapping_phase(self):
        from fitz_forge.models.events import describe_phase

        assert describe_phase("agent:mapping") == "Mapping codebase..."

    def test_agent_selecting_phase(self):
        from fitz_forge.models.events import describe_phase

        assert describe_phase("agent:selecting") == "Selecting relevant files..."

    def test_agent_summarizing_phase(self):
        from fitz_forge.models.events import describe_phase

        desc = describe_phase("agent:summarizing:src/main.py")
        assert desc == "Summarizing main.py..."

    def test_agent_synthesizing_phase(self):
        from fitz_forge.models.events import describe_phase

        assert describe_phase("agent:synthesizing") == "Synthesizing context..."

    def test_bare_stage_name_maps_to_reasoning(self):
        from fitz_forge.models.events import describe_phase

        desc = describe_phase("context")
        assert desc == "Analyzing requirements and constraints..."

    def test_empty_phase_returns_empty(self):
        from fitz_forge.models.events import describe_phase

        assert describe_phase("") == ""
        assert describe_phase(None) == ""

    def test_unknown_phase_returns_as_is(self):
        from fitz_forge.models.events import describe_phase

        assert describe_phase("some_unknown_thing") == "some_unknown_thing"

    def test_all_stages_have_reasoning(self):
        """All 3 pipeline stages should have reasoning descriptions."""
        from fitz_forge.models.events import _PHASE_DESCRIPTIONS

        for stage in ("context", "architecture_design", "roadmap_risk"):
            assert f"{stage}:reasoning" in _PHASE_DESCRIPTIONS, f"Missing {stage}:reasoning"


class TestFormatEvent:
    """Tests for cli._format_event rendering each PlanEvent type."""

    def test_phase_changed(self):
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import PhaseChanged

        line = _format_event(
            PhaseChanged(
                job_id="abc",
                progress=0.42,
                phase="architecture_design:reasoning",
                description="Exploring architecture and design...",
            )
        )
        assert "42%" in line
        assert "Exploring architecture and design" in line

    def test_completed(self):
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import JobCompleted

        line = _format_event(
            JobCompleted(
                job_id="abc",
                file_path="/tmp/plan.md",
                quality_score=58.0,
                elapsed_s=272.0,
                max_quality_score=70,
                quality_applicable=True,
            )
        )
        assert "Done" in line
        assert "58/70" in line
        assert "4m32s" in line

    def test_completed_without_quality_renders_em_dash(self):
        """applicable=False (e.g. short-circuit: no artifacts) -> em-dash."""
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import JobCompleted

        line = _format_event(
            JobCompleted(
                job_id="abc",
                file_path=None,
                quality_score=None,
                elapsed_s=5.0,
                quality_applicable=False,
            )
        )
        assert "—" in line

    def test_completed_with_scoring_error_renders_question_mark(self):
        """Scoring failed but plan succeeded -> ? so user sees something odd."""
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import JobCompleted

        line = _format_event(
            JobCompleted(
                job_id="abc",
                file_path="/tmp/plan.md",
                quality_score=None,
                elapsed_s=5.0,
                quality_applicable=True,
                quality_error="boom",
            )
        )
        assert "?" in line

    def test_completed_with_fractional_quality_uses_one_decimal(self):
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import JobCompleted

        line = _format_event(
            JobCompleted(
                job_id="abc",
                file_path="/tmp/plan.md",
                quality_score=58.3,
                elapsed_s=10.0,
                max_quality_score=70,
            )
        )
        assert "58.3/70" in line

    def test_awaiting_review(self):
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import JobAwaitingReview

        line = _format_event(JobAwaitingReview(job_id="abc", elapsed_s=10.0))
        assert "Awaiting review" in line
        assert "fitz-forge confirm abc" in line

    def test_failed(self):
        from fitz_forge.cli import _format_event
        from fitz_forge.models.events import JobFailed

        line = _format_event(
            JobFailed(job_id="abc", error="boom", elapsed_s=7.0)
        )
        assert "Failed" in line
        assert "boom" in line
