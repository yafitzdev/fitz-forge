# fitz_forge/tools/run_plan.py
"""
run_plan tool — the streaming service for inline (CLI) job execution.

Owns the short-lived dependencies that a single planning run needs
(LLM client, BackgroundWorker) and exposes the job as an async stream
of typed PlanEvents. The CLI consumes this stream to render a live
feed; MCP is free to consume it too later on.

SQLite continues to be the source of truth for durable state; events
are ephemeral and only exist while this generator is alive.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fitz_forge.config.schema import FitzPlannerConfig
from fitz_forge.models.events import (
    JobAwaitingReview,
    JobCompleted,
    JobFailed,
    PlanEvent,
)
from fitz_forge.models.store import JobStore

logger = logging.getLogger(__name__)


async def run_plan(
    job_id: str,
    store: JobStore,
    config: FitzPlannerConfig,
    pre_gathered_context: str | None = None,
    resume: bool = False,
) -> AsyncIterator[PlanEvent]:
    """
    Execute a queued job and stream PlanEvents as they happen.

    Yields events until a terminal event (JobCompleted / JobAwaitingReview
    / JobFailed) has been delivered. Manages LLM client startup/shutdown
    internally.

    Args:
        job_id: The job to run.
        store: Durable job store (used by worker for state + checkpointing).
        config: Loaded planner configuration.
        pre_gathered_context: Optional context string from a prior --clarify
            agent pass, forwarded to the worker so the agent isn't re-run.
        resume: If True, resume from checkpoint instead of starting fresh.

    Yields:
        PlanEvent objects in occurrence order. The final event is terminal.

    Raises:
        The underlying exception from the worker run is re-raised **after**
        the corresponding JobFailed event has been yielded, so the consumer
        always sees a terminal event before an exception propagates.
    """
    from fitz_forge.background.worker import BackgroundWorker
    from fitz_forge.llm.factory import create_llm_client
    from fitz_forge.llm.llama_cpp import LlamaCppClient

    queue: asyncio.Queue[PlanEvent] = asyncio.Queue()

    async def emitter(event: PlanEvent) -> None:
        await queue.put(event)

    client = create_llm_client(config)
    if isinstance(client, LlamaCppClient):
        await client.start()

    worker = BackgroundWorker(
        store,
        config=config,
        ollama_client=client,
        memory_threshold=config.ollama.memory_threshold,
        event_emitter=emitter,
    )

    job_task = asyncio.create_task(
        worker.process_job_direct(
            job_id,
            pre_gathered_context=pre_gathered_context,
            resume=resume,
        )
    )

    pending_exception: BaseException | None = None

    async def _next_event() -> PlanEvent | None:
        """Return next event, or None if worker finished without emitting more."""
        get_task = asyncio.create_task(queue.get())
        done, _pending = await asyncio.wait(
            {get_task, job_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if get_task in done:
            return get_task.result()
        # job_task finished before a new event showed up — drain remaining events
        get_task.cancel()
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    try:
        while True:
            event = await _next_event()
            if event is None:
                # Worker done and queue drained. Exit loop; exception handling below.
                break
            yield event
            if isinstance(event, (JobCompleted, JobAwaitingReview, JobFailed)):
                break

        if not job_task.done():
            await job_task
        pending_exception = job_task.exception()
    except (KeyboardInterrupt, asyncio.CancelledError):
        job_task.cancel()
        try:
            await job_task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    finally:
        if isinstance(client, LlamaCppClient):
            try:
                await client.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"LlamaCpp stop failed: {e}")

    if pending_exception is not None:
        raise pending_exception
