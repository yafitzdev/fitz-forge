# tests/unit/test_synthesis_artifact_coverage_wiring.py
"""Tests for the synthesis stage's artifact-coverage review pass.

After per-file artifact generation, the stage checks that every
filename from ``context.needed_artifacts`` shipped as an artifact. When
files are missing, the stage regenerates them via
``_build_missing_artifacts`` with the original purpose. Remaining gaps
are returned as ``ReviewIssue`` so the caller surfaces them on
``design.review_findings``.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage


@pytest.fixture
def stage():
    return SynthesisStage()


# ---------------------------------------------------------------------------
# No needed_artifacts → review skipped, artifacts returned as-is
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_needed_artifacts_skips(monkeypatch, stage):
    async def fake_build_missing(*args, **kwargs):  # pragma: no cover
        raise AssertionError("regen must not run when needed_artifacts empty")

    monkeypatch.setattr(stage, "_build_missing_artifacts", fake_build_missing)

    artifacts = [{"filename": "x.py", "content": "x"}]
    result, issues = await stage._artifact_coverage_review_pass(
        client=object(),
        prior_outputs={},
        context_merged={},
        artifacts=artifacts,
        reasoning="r",
    )
    assert result is artifacts
    assert issues == []


# ---------------------------------------------------------------------------
# All needed files present → no regen, no issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_produced_no_regen(monkeypatch, stage):
    async def fake_build_missing(*args, **kwargs):  # pragma: no cover
        raise AssertionError("regen must not run when coverage is complete")

    monkeypatch.setattr(stage, "_build_missing_artifacts", fake_build_missing)

    context_merged = {
        "needed_artifacts": [
            "src/a.py -- do a",
            "src/b.py -- do b",
        ]
    }
    artifacts = [
        {"filename": "src/a.py", "content": "..."},
        {"filename": "src/b.py", "content": "..."},
    ]
    result, issues = await stage._artifact_coverage_review_pass(
        client=object(),
        prior_outputs={},
        context_merged=context_merged,
        artifacts=artifacts,
        reasoning="r",
    )
    assert result == artifacts
    assert issues == []


# ---------------------------------------------------------------------------
# Missing file → regen fills it, no remaining issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_regen_fills_all(monkeypatch, stage):
    received_specs: list[list[tuple[str, str]]] = []

    async def fake_build_missing(client, missing_specs, reasoning, prior_outputs):
        received_specs.append(list(missing_specs))
        return [
            {"filename": fn, "content": f"generated for {fn}", "purpose": p}
            for fn, p in missing_specs
        ]

    monkeypatch.setattr(stage, "_build_missing_artifacts", fake_build_missing)

    context_merged = {
        "needed_artifacts": [
            "src/a.py -- do a",
            "src/b.py -- do b",
            "src/c.py -- do c",
        ]
    }
    artifacts = [{"filename": "src/a.py", "content": "ok"}]

    result, issues = await stage._artifact_coverage_review_pass(
        client=object(),
        prior_outputs={},
        context_merged=context_merged,
        artifacts=artifacts,
        reasoning="r",
    )

    # Regen was called with the two missing files + their original purpose.
    assert len(received_specs) == 1
    assert dict(received_specs[0]) == {"src/b.py": "do b", "src/c.py": "do c"}

    # Final artifacts include all three files.
    fns = {a["filename"] for a in result}
    assert fns == {"src/a.py", "src/b.py", "src/c.py"}
    assert issues == []


# ---------------------------------------------------------------------------
# Partial regen → remaining files flagged as issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_regen_surfaces_remaining(monkeypatch, stage):
    async def fake_build_missing(client, missing_specs, reasoning, prior_outputs):
        # Only manage to build one of the two.
        return [{"filename": "src/b.py", "content": "built"}]

    monkeypatch.setattr(stage, "_build_missing_artifacts", fake_build_missing)

    context_merged = {
        "needed_artifacts": [
            "src/a.py",
            "src/b.py",
            "src/c.py -- final one",
        ]
    }
    artifacts = [{"filename": "src/a.py", "content": "ok"}]

    result, issues = await stage._artifact_coverage_review_pass(
        client=object(),
        prior_outputs={},
        context_merged=context_merged,
        artifacts=artifacts,
        reasoning="r",
    )

    fns = {a["filename"] for a in result}
    assert fns == {"src/a.py", "src/b.py"}
    assert len(issues) == 1
    assert issues[0].target == "src/c.py"


# ---------------------------------------------------------------------------
# Regen raises → original artifacts kept, all originally-missing flagged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_failure_surfaces_all_missing(monkeypatch, stage):
    async def exploding_build_missing(client, missing_specs, reasoning, prior_outputs):
        raise RuntimeError("artifact gen down")

    monkeypatch.setattr(stage, "_build_missing_artifacts", exploding_build_missing)

    context_merged = {
        "needed_artifacts": [
            "src/a.py",
            "src/b.py",
            "src/c.py",
        ]
    }
    artifacts = [{"filename": "src/a.py", "content": "ok"}]

    result, issues = await stage._artifact_coverage_review_pass(
        client=object(),
        prior_outputs={},
        context_merged=context_merged,
        artifacts=artifacts,
        reasoning="r",
    )

    assert result is artifacts
    assert {i.target for i in issues} == {"src/b.py", "src/c.py"}
