# benchmarks/decomp_replay.py
"""Run DecisionDecompositionStage in isolation against a pre-stages snapshot.

Used to assess how the senior-review pass changes decomposition output
without paying for the full pipeline (resolution + synthesis ~20-40 min).

Usage:
    .venv/Scripts/python -m benchmarks.decomp_replay \\
        --snapshot path/to/snapshot_after__pre_stages.json \\
        --query "task description"

Emits a JSON side-by-side with the original decomposition found next to
the snapshot (traces_XX/004_generate.json) so you can see what changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import typer

from fitz_forge.config import load_config
from fitz_forge.llm.factory import create_llm_client
from fitz_forge.llm.generate import configure_tracing
from fitz_forge.planning.pipeline.stages.decision_decomposition import (
    DecisionDecompositionStage,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("decomp_replay")

app = typer.Typer()


def _load_snapshot(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def _find_original_decomposition(snapshot_path: Path) -> list[dict] | None:
    """Find the original decomposition output next to the snapshot, if any."""
    traces_dir = snapshot_path.parent
    # Prior-run produces a ``decision_decomposition`` key in the run-output,
    # but we can also pull from the raw generate trace. Prefer snapshot
    # sibling ``snapshot_after_decision_decomposition.json`` when present.
    sibling = traces_dir / "snapshot_after_decision_decomposition.json"
    if sibling.is_file():
        try:
            data = json.loads(sibling.read_text(encoding="utf-8"))
            return data.get("decision_decomposition", {}).get("decisions")
        except Exception:
            pass
    return None


def _summarize_decisions(decisions: list[dict]) -> str:
    lines = []
    for d in decisions:
        did = d.get("id", "?")
        category = d.get("category", "?")
        question = d.get("question", "")[:180]
        lines.append(f"  {did} ({category}): {question}")
    return "\n".join(lines)


@app.command()
def main(
    snapshot: str = typer.Option(
        ..., help="Path to snapshot_after__pre_stages.json"
    ),
    query: str = typer.Option(
        ..., help="Task description (same as original benchmark run)"
    ),
    out: str = typer.Option("", help="Write decomposition JSON here (default: alongside snapshot)"),
) -> None:
    snap_path = Path(snapshot).resolve()
    if not snap_path.is_file():
        raise SystemExit(f"snapshot not found: {snap_path}")

    prior_outputs = _load_snapshot(snap_path)
    logger.info(f"snapshot loaded: {snap_path.name} ({len(prior_outputs)} keys)")

    config = load_config()
    client = create_llm_client(config)

    trace_dir = snap_path.parent.parent / "traces_decomp_replay"
    trace_dir.mkdir(parents=True, exist_ok=True)
    configure_tracing(trace_dir)

    stage = DecisionDecompositionStage()

    async def _run() -> dict:
        return await stage.execute(
            client=client,
            job_description=query,
            prior_outputs=prior_outputs,
        )

    result = asyncio.run(_run())
    configure_tracing(None)

    if not result.success:
        raise SystemExit(f"stage failed: {result.error}")

    new_decisions = result.output.get("decisions", [])
    print("\n=== NEW decomposition (with senior review) ===")
    print(_summarize_decisions(new_decisions))
    print(f"\n  count: {len(new_decisions)}")

    original = _find_original_decomposition(snap_path)
    if original is not None:
        print("\n=== ORIGINAL decomposition (pre-refactor) ===")
        print(_summarize_decisions(original))
        print(f"\n  count: {len(original)}")

    out_path = Path(out) if out else snap_path.parent / "decomp_replay_output.json"
    out_path.write_text(
        json.dumps({"new": result.output, "original": original}, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    app()
