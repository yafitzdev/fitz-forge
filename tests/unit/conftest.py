# tests/unit/conftest.py
"""
Shared fixtures for unit tests.

Provides reusable store, job record, and mock LLM client fixtures
used across multiple test files.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from fitz_forge.models.jobs import (
    InMemoryJobStore,
    JobRecord,
    JobState,
    generate_job_id,
)
from fitz_forge.models.sqlite_store import SQLiteJobStore


# ---------------------------------------------------------------------------
# SQLite store (tmp_path backed)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_store(tmp_path: Path) -> SQLiteJobStore:
    """Create and initialize a temporary SQLiteJobStore.

    Used by: test_sqlite_store, test_tools_integration, test_worker,
    test_worker_ollama, test_worker_pipeline, test_pipeline_integration.
    """
    db_path = str(tmp_path / "test_jobs.db")
    s = SQLiteJobStore(db_path)
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def memory_store() -> InMemoryJobStore:
    """Create an in-memory job store.

    Used by: test_cancel_review, test_confirm_review.
    """
    return InMemoryJobStore()


# ---------------------------------------------------------------------------
# Job record factories
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_job() -> JobRecord:
    """A fully-populated sample job record (QUEUED state)."""
    return JobRecord(
        job_id="test-job-001",
        description="Test planning job",
        timeline="Q1 2024",
        context="Test context for planning",
        integration_points=["fitz-ai", "local-llm"],
        state=JobState.QUEUED,
        progress=0.0,
        current_phase=None,
        quality_score=None,
        created_at=datetime.now(timezone.utc),
        file_path=None,
        error=None,
        updated_at=None,
    )


@pytest.fixture
def make_job():
    """Factory fixture for creating JobRecords with custom overrides.

    Usage::

        def test_something(make_job):
            job = make_job(state=JobState.FAILED, error="OOM")
    """
    def _factory(
        job_id: str | None = None,
        description: str = "Test planning job",
        timeline: str | None = None,
        context: str | None = None,
        integration_points: list[str] | None = None,
        state: JobState = JobState.QUEUED,
        progress: float = 0.0,
        current_phase: str | None = None,
        quality_score: float | None = None,
        created_at: datetime | None = None,
        file_path: str | None = None,
        error: str | None = None,
        api_review: bool = False,
        cost_estimate_json: str | None = None,
    ) -> JobRecord:
        return JobRecord(
            job_id=job_id or generate_job_id(),
            description=description,
            timeline=timeline,
            context=context,
            integration_points=integration_points or [],
            state=state,
            progress=progress,
            current_phase=current_phase,
            quality_score=quality_score,
            created_at=created_at or datetime.now(timezone.utc),
            file_path=file_path,
            error=error,
            api_review=api_review,
            cost_estimate_json=cost_estimate_json,
        )

    return _factory


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockLLMClient:
    """Configurable mock LLM client for testing.

    Supports preset responses (single value or list of sequential responses),
    call counting, and error simulation.

    Usage::

        client = MockLLMClient(response="yes")
        client = MockLLMClient(responses=["first", "second", "third"])
        client = MockLLMClient(response="error")  # raises RuntimeError
    """

    def __init__(
        self,
        response: str = "{}",
        responses: list[str] | None = None,
    ):
        self._responses = responses or [response]
        self.call_count = 0
        self.calls: list[list[dict]] = []

    async def generate(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> str:
        self.calls.append(messages)
        idx = self.call_count
        self.call_count += 1
        text = self._responses[idx % len(self._responses)]
        if text == "error":
            raise RuntimeError("Mock LLM error")
        return text

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def mock_llm_client():
    """Factory fixture for MockLLMClient.

    Usage::

        def test_something(mock_llm_client):
            client = mock_llm_client(response="yes")
            client = mock_llm_client(responses=["a", "b", "c"])
    """
    def _factory(response: str = "{}", responses: list[str] | None = None):
        return MockLLMClient(response=response, responses=responses)
    return _factory


@pytest.fixture
def async_mock_llm_client():
    """An AsyncMock-based LLM client for patching.

    Returns an AsyncMock with health_check and generate pre-configured.
    Suitable for tests that use ``patch.object`` or need ``side_effect``.
    """
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=True)
    client.generate = AsyncMock(return_value="{}")
    return client
