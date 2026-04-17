# fitz_forge/cli.py
"""
CLI interface for fitz-forge.

Thin presentation layer over the tools/ service layer.
All commands delegate to the same functions that MCP wraps.
"""

import asyncio
import sys
import time

import typer

from fitz_forge.models.events import (
    DecisionHallucinationDropped,
    DecisionResolved,
    JobAwaitingReview,
    JobCompleted,
    JobFailed,
    PhaseChanged,
    PlanEvent,
)

__all__ = ["app"]


def _fmt_duration(seconds: float) -> str:
    """Format seconds as human-readable duration (e.g. '5m17s', '42s')."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m{s:02d}s"

app = typer.Typer(
    name="fitz-forge",
    help="Local-first AI architectural planning using local LLMs.",
    no_args_is_help=True,
)


def _run(coro):
    """Run async function from sync CLI context."""
    return asyncio.run(coro)


async def _get_store():
    """Open SQLiteJobStore directly (no lifecycle needed for read-only ops)."""
    from platformdirs import user_config_path

    from fitz_forge.models.sqlite_store import SQLiteJobStore

    config_dir = user_config_path("fitz-forge", ensure_exists=True)
    store = SQLiteJobStore(str(config_dir / "jobs.db"))
    await store.initialize()
    return store


async def _find_last_job(store) -> str | None:
    """Find the most recently created job with agent context."""
    import aiosqlite

    async with aiosqlite.connect(store._db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM jobs WHERE pipeline_state IS NOT NULL "
            "AND pipeline_state LIKE '%_agent_context%' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return row[0] if row else None


def _state_color(state: str) -> str:
    """Return ANSI color for job state."""
    colors = {
        "complete": typer.colors.GREEN,
        "running": typer.colors.YELLOW,
        "queued": typer.colors.CYAN,
        "awaiting_review": typer.colors.MAGENTA,
        "failed": typer.colors.RED,
        "interrupted": typer.colors.RED,
    }
    return colors.get(state, typer.colors.WHITE)



async def _gather_context_for_clarification(
    client, config, description: str, source_dir: str, console
) -> str:
    """Run agent context gathering standalone for use in clarification flow."""
    from pathlib import Path

    from rich.status import Status

    from fitz_forge.planning.agent import AgentContextGatherer

    if not Path(source_dir).is_dir():
        return ""

    agent = AgentContextGatherer(config=config.agent, source_dir=source_dir)
    with Status("[dim]Reading project...[/dim]", console=console, spinner="dots"):
        gathered = await agent.gather(client=client, job_description=description)
    # gather() returns {"synthesized": str, "raw_summaries": str} — extract synthesized for clarification
    if isinstance(gathered, dict):
        return gathered.get("synthesized", "")
    return gathered


def _format_event(event: PlanEvent) -> str:
    """Render a single PlanEvent as a rich-markup console line."""
    ts = time.strftime("%H:%M:%S")
    if isinstance(event, PhaseChanged):
        pct = f"{event.progress * 100:3.0f}%"
        desc = event.description or event.phase
        return f"[dim]{ts}[/dim] [cyan]{pct}[/cyan]  {desc}"
    if isinstance(event, DecisionResolved):
        # Indented dim bullet so it reads as a sub-line under the stage bar.
        # Format: "    · d5: Add StreamingResponse route → fitz_sage/api/routes/collections.py"
        line = f"    [dim]· {event.decision_id}: {event.summary}"
        if event.target_file:
            line += f" → {event.target_file}"
        line += "[/dim]"
        return line
    if isinstance(event, DecisionHallucinationDropped):
        snippet = event.evidence_snippet
        # Keep first ~80 chars for readability
        if len(snippet) > 80:
            snippet = snippet[:77].rstrip() + "..."
        return (
            f"    [dim]· {event.decision_id}: dropped hallucinated evidence: "
            f"{snippet}[/dim]"
        )
    if isinstance(event, JobCompleted):
        # Three renderings:
        #   - n/a (em-dash): plan succeeded but had no artifacts to score
        #     (e.g. implementation-already-exists short-circuit).
        #   - ?: scoring raised an error; plan is still fine.
        #   - 58/70: live deterministic score (artifact_quality + consistency).
        if not event.quality_applicable:
            quality = "—"
        elif event.quality_error is not None:
            quality = "?"
        elif event.quality_score is None:
            quality = "—"
        else:
            max_q = event.max_quality_score or 70
            # Integer display when clean; one decimal when fractional.
            if float(event.quality_score).is_integer():
                quality = f"{int(event.quality_score)}/{max_q}"
            else:
                quality = f"{event.quality_score:.1f}/{max_q}"
        return (
            f"\n[green]✓ Done[/green]  quality: {quality}  "
            f"time: {_fmt_duration(event.elapsed_s)}"
        )
    if isinstance(event, JobAwaitingReview):
        return (
            f"\n[magenta]⏸ Awaiting review[/magenta]  "
            f"Run 'fitz-forge confirm {event.job_id}' to proceed."
        )
    if isinstance(event, JobFailed):
        return f"\n[red]✗ Failed[/red]: {event.error}"
    return str(event)


async def _run_inline(
    job_id: str,
    store,
    config,
    description: str,
    pre_gathered_context: str | None = None,
    resume: bool = False,
    console=None,
) -> None:
    """Run a job inline, streaming status lines as they arrive."""
    from rich.console import Console

    from fitz_forge.tools.run_plan import run_plan

    if console is None:
        console = Console(stderr=True)

    heading = description[:80] + ("…" if len(description) > 80 else "")
    console.print(f"[bold]{heading}[/bold]  [dim]({job_id})[/dim]")

    completed_cleanly = False
    failed_event: JobFailed | None = None

    try:
        async for event in run_plan(
            job_id=job_id,
            store=store,
            config=config,
            pre_gathered_context=pre_gathered_context,
            resume=resume,
        ):
            console.print(_format_event(event))
            if isinstance(event, JobCompleted):
                if event.file_path:
                    console.print(f"[dim]Saved:[/dim] {event.file_path}")
                    console.print(f"[dim]View :[/dim]  fitz-forge get {job_id}")
                console.print()
                completed_cleanly = True
            elif isinstance(event, JobAwaitingReview):
                completed_cleanly = True
            elif isinstance(event, JobFailed):
                failed_event = event
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise KeyboardInterrupt from None

    if failed_event is not None:
        raise typer.Exit(1)
    if not completed_cleanly:
        # Worker exited without emitting a terminal event — treat as failure.
        console.print("[red]✗ Failed[/red]: job ended without terminal event")
        raise typer.Exit(1)


@app.command()
def plan(
    description: str = typer.Argument(..., help="What you want to build or accomplish"),
    timeline: str = typer.Option(None, "--timeline", "-t", help="Timeline constraints"),
    context: str = typer.Option(None, "--context", "-c", help="Additional context"),
    api_review: bool = typer.Option(False, "--api-review", help="Enable API review"),
    source_dir: str = typer.Option(None, "--source-dir", help="Path to codebase for agent context"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Queue only, don't run inline"),
    clarify: bool = typer.Option(
        False, "--clarify", help="Ask clarifying questions before planning"
    ),
):
    """Queue and run a planning job with live progress. Use --detach to queue only."""
    from fitz_forge.config.loader import load_config
    from fitz_forge.tools.create_plan import create_plan

    async def _plan():
        import logging as _logging

        from rich.console import Console as _Console

        config = load_config()
        enriched_description = description
        pre_gathered_context: str | None = None
        console = _Console(stderr=True)

        # Clarification flow needs interactive terminal — runs before the stream
        if clarify and not detach and sys.stdin.isatty():
            try:
                from fitz_forge.llm.factory import create_llm_client
                from fitz_forge.planning.clarification import get_clarifying_questions

                client = create_llm_client(config)
                if await client.health_check():
                    effective_source_dir = source_dir or config.agent.source_dir or "."
                    if config.agent.enabled:
                        pre_gathered_context = await _gather_context_for_clarification(
                            client, config, description, effective_source_dir, console
                        )

                    questions = await get_clarifying_questions(
                        client, description, codebase_context=pre_gathered_context or ""
                    )
                    if questions:
                        console.print("\n[bold]A few quick questions to sharpen the plan:[/bold]\n")
                        answers = []
                        for i, q in enumerate(questions, 1):
                            answer = typer.prompt(f"  {i}. {q}", default="")
                            if answer.strip():
                                answers.append(f"Q: {q}\nA: {answer}")
                        if answers:
                            enriched_description = (
                                description + "\n\n## Clarifications\n\n" + "\n\n".join(answers)
                            )
                        typer.echo()
            except Exception as e:
                _logging.getLogger(__name__).warning(f"Clarification skipped: {e}")

        if detach:
            store = await _get_store()
            try:
                result = await create_plan(
                    description=enriched_description,
                    timeline=timeline,
                    context=context,
                    integration_points=None,
                    api_review=api_review,
                    store=store,
                    config=config,
                    source_dir=source_dir,
                )
            finally:
                await store.close()
            typer.echo(f"Queued job {result['job_id']}. Run 'fitz-forge run' to start processing.")
            return

        store = await _get_store()
        try:
            result = await create_plan(
                description=enriched_description,
                timeline=timeline,
                context=context,
                integration_points=None,
                api_review=api_review,
                store=store,
                config=config,
                source_dir=source_dir,
            )
            job_id = result["job_id"]

            await _run_inline(
                job_id,
                store,
                config,
                enriched_description,
                pre_gathered_context=pre_gathered_context,
                console=console,
            )
        finally:
            await store.close()

    try:
        _run(_plan())
    except KeyboardInterrupt:
        typer.echo("\nCancelled.", err=True)
        raise typer.Exit(130) from None
    except RuntimeError as e:
        msg = str(e)
        if "Failed to load model" in msg:
            model = msg.split('"')[1] if '"' in msg else "unknown"
            typer.echo(
                f"\nERROR: Could not load model '{model}'. "
                f"Try restarting LM Studio and running again.",
                err=True,
            )
        elif "Pipeline failed" in msg:
            typer.echo(f"\nERROR: {msg}", err=True)
        else:
            typer.echo(f"\nERROR: {msg}", err=True)
        raise typer.Exit(1) from None
    except ConnectionError as e:
        typer.echo(f"\nERROR: LLM server not reachable. Is LM Studio running?\n  {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"\nERROR: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(1) from None


@app.command("run")
def run_worker():
    """Start the worker to process queued jobs. Ctrl+C to stop."""
    import logging
    import sys

    from platformdirs import user_config_path

    from fitz_forge.background.lifecycle import ServerLifecycle
    from fitz_forge.config.loader import load_config

    # Simple human-readable logging to stderr for CLI mode
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    config = load_config()
    config_dir = user_config_path("fitz-forge", ensure_exists=True)
    db_path = str(config_dir / "jobs.db")

    async def _run_worker():
        lifecycle = ServerLifecycle(db_path, config=config)
        await lifecycle.startup()
        typer.echo("Worker started. Processing queued jobs... (Ctrl+C to stop)\n")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            typer.echo("\nShutting down...")
            await lifecycle.shutdown()

    try:
        _run(_run_worker())
    except KeyboardInterrupt:
        pass


@app.command("list")
def list_jobs():
    """List all planning jobs."""
    from fitz_forge.tools.list_plans import list_plans

    async def _list():
        store = await _get_store()
        try:
            return await list_plans(store=store)
        finally:
            await store.close()

    result = _run(_list())
    plans = result["plans"]

    if not plans:
        typer.echo("No plans found.")
        return

    # Print table header
    typer.echo(f"{'JOB ID':<14} {'STATE':<18} {'QUALITY':<9} DESCRIPTION")
    typer.echo("-" * 80)

    for p in plans:
        state = p["state"]
        quality = f"{p['quality_score']:.2f}" if p["quality_score"] is not None else "-"
        desc = p["description"]

        # Derive project name from plan file path (.../project/.fitz-forge/plans/plan_*.md)
        project = ""
        if p.get("file_path"):
            from pathlib import Path as _Path

            parts = _Path(p["file_path"]).parts
            # Find the part before .fitz-forge
            for i, part in enumerate(parts):
                if part == ".fitz-forge" and i > 0:
                    project = parts[i - 1]
                    break

        typer.echo(
            typer.style(f"{p['job_id']:<14} ", fg=_state_color(state))
            + typer.style(f"{state:<18} ", fg=_state_color(state))
            + f"{quality:<9} {desc}"
        )
        if project:
            typer.echo(
                f"{'':14} {'':18} {'':9} "
                + typer.style(f"↳ {project}", fg=typer.colors.BRIGHT_BLACK)
            )


@app.command()
def status(job_id: str = typer.Argument(..., help="Job ID to check")):
    """Check the status of a planning job."""
    from fitz_forge.tools.check_status import check_status

    async def _status():
        store = await _get_store()
        try:
            return await check_status(job_id, store=store)
        finally:
            await store.close()

    try:
        result = _run(_status())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    state = result["state"]
    typer.echo(f"Job:      {result['job_id']}")
    typer.echo(typer.style(f"State:    {state}", fg=_state_color(state)))
    typer.echo(f"Progress: {result['progress'] * 100:.0f}%")
    if result.get("current_phase"):
        typer.echo(f"Phase:    {result['current_phase']}")
    if result.get("message"):
        typer.echo(f"Message:  {result['message']}")
    if result.get("error"):
        typer.echo(typer.style(f"Error:    {result['error']}", fg=typer.colors.RED))


@app.command()
def get(
    job_id: str = typer.Argument(..., help="Job ID to retrieve"),
    format: str = typer.Option(
        "full", "--format", "-f", help="Output format: full, summary, roadmap_only"
    ),
):
    """Retrieve a completed plan."""
    from fitz_forge.tools.get_plan import get_plan

    async def _get():
        store = await _get_store()
        try:
            return await get_plan(job_id, format, store=store)
        finally:
            await store.close()

    try:
        result = _run(_get())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Print raw markdown to stdout (pipeable)
    typer.echo(result["content"])


@app.command()
def resume(job_id: str = typer.Argument(..., help="Job ID to resume")):
    """Resume a failed or interrupted job with live progress display."""
    from fitz_forge.config.loader import load_config
    from fitz_forge.tools.retry_job import retry_job

    async def _resume():
        store = await _get_store()
        config = load_config()

        job = await store.get(job_id)
        if not job:
            await store.close()
            typer.echo(f"Job '{job_id}' not found.", err=True)
            raise typer.Exit(1)

        # Re-queue if needed (failed/interrupted), skip if already queued
        if job.state.value in ("failed", "interrupted"):
            await retry_job(job_id, store=store)
        elif job.state.value == "running":
            # Stale running state from killed worker — reset to queued
            from fitz_forge.models.jobs import JobState

            await store.update(
                job_id, state=JobState.QUEUED, progress=0.0, error=None, current_phase=None
            )
        elif job.state.value == "complete":
            await store.close()
            typer.echo(
                f"Job '{job_id}' is already complete. Use 'fitz-forge get {job_id}' to view.",
                err=True,
            )
            raise typer.Exit(1)
        # queued / awaiting_review — just run it

        description = job.description

        try:
            await _run_inline(job_id, store, config, description, resume=True)
        finally:
            await store.close()

    try:
        _run(_resume())
    except KeyboardInterrupt:
        typer.echo("\nCancelled.", err=True)
        raise typer.Exit(130) from None


@app.command()
def retry(job_id: str = typer.Argument(..., help="Job ID to retry")):
    """Retry a failed or interrupted job (queue only, use 'resume' for live UI)."""
    from fitz_forge.tools.retry_job import retry_job

    async def _retry():
        store = await _get_store()
        try:
            return await retry_job(job_id, store=store)
        finally:
            await store.close()

    try:
        result = _run(_retry())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"Job {result['job_id']} re-queued. Run 'fitz-forge resume {result['job_id']}' or 'fitz-forge run' to process."
    )


@app.command()
def confirm(job_id: str = typer.Argument(..., help="Job ID to approve API review")):
    """Approve API review after seeing cost estimate."""
    from fitz_forge.tools.confirm_review import confirm_review

    async def _confirm():
        store = await _get_store()
        try:
            return await confirm_review(job_id, store=store)
        finally:
            await store.close()

    try:
        result = _run(_confirm())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"API review approved for {result['job_id']}. Run 'fitz-forge run' to process.")


@app.command()
def cancel(job_id: str = typer.Argument(..., help="Job ID to skip API review")):
    """Skip API review, finalize plan without it."""
    from fitz_forge.tools.cancel_review import cancel_review

    async def _cancel():
        store = await _get_store()
        try:
            return await cancel_review(job_id, store=store)
        finally:
            await store.close()

    try:
        result = _run(_cancel())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"API review skipped for {result['job_id']}. Plan finalized.")


@app.command()
def serve():
    """Start the MCP server (for Claude Code integration)."""
    from fitz_forge.__main__ import main

    asyncio.run(main())


@app.command()
def prep(
    base_url: str = typer.Option(None, "--base-url", help="API base URL (skip probe)"),
    model: str = typer.Option(None, "--model", help="Model identifier (skip model prompt)"),
):
    """First-run setup wizard: detect API server, pick model, write config."""
    from fitz_forge.config.loader import get_config_path
    from fitz_forge.config.prep import run_wizard

    config_path = get_config_path()
    try:
        _run(run_wizard(config_path, base_url=base_url, model=model))
    except KeyboardInterrupt:
        typer.echo("\nSetup aborted.", err=True)
        raise typer.Exit(130) from None


@app.command()
def replay(
    job_id: str = typer.Argument("last", help="Job ID to replay from, or 'last' for most recent"),
):
    """Re-run planning stages using agent context from a completed job.

    Skips the expensive codebase exploration (~15-20 min) and re-runs
    only the planning stages (~10 min). Useful for testing pipeline
    changes without re-gathering context.

    Use 'fitz-forge replay last' (or just 'fitz-forge replay')
    to replay the most recent completed job.
    """
    from fitz_forge.config.loader import load_config
    from fitz_forge.tools.replay_plan import replay_plan

    async def _replay():
        store = await _get_store()
        config = load_config()

        resolved_id = job_id
        if resolved_id == "last":
            resolved_id = await _find_last_job(store)
            if not resolved_id:
                typer.echo("No completed jobs found.", err=True)
                raise typer.Exit(1)

        try:
            result = await replay_plan(
                source_job_id=resolved_id,
                store=store,
                db_path=store._db_path,
            )
            new_job_id = result["job_id"]
            typer.echo(
                f"Created replay job {new_job_id} from {resolved_id} "
                f"(reusing agent context, re-running planning stages)",
                err=True,
            )

            await _run_inline(new_job_id, store, config, result["description"], resume=True)
        finally:
            await store.close()

    try:
        _run(_replay())
    except KeyboardInterrupt:
        typer.echo("\nCancelled.", err=True)
        raise typer.Exit(130) from None


@app.command()
def purge(
    include_complete: bool = typer.Option(False, "--all", help="Also remove completed jobs"),
):
    """Kill all zombie jobs (interrupted, queued, running, failed)."""

    async def _purge():
        import aiosqlite

        store = await _get_store()
        try:
            states = ["interrupted", "queued", "running", "failed"]
            if include_complete:
                states.append("complete")

            placeholders = ",".join("?" for _ in states)
            async with aiosqlite.connect(store._db_path) as db:
                cursor = await db.execute(
                    f"UPDATE jobs SET state='failed' WHERE state IN ({placeholders})",
                    states,
                )
                await db.commit()
                return cursor.rowcount
        finally:
            await store.close()

    count = _run(_purge())
    if count:
        typer.echo(f"Purged {count} job(s).")
    else:
        typer.echo("No jobs to purge.")


if __name__ == "__main__":
    app()
