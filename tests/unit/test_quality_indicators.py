# tests/unit/test_quality_indicators.py
"""Tests for the runtime quality-indicator module."""

from __future__ import annotations

from fitz_forge.planning.quality.indicators import (
    QualityIndicators,
    _parse_needed_entry,
    compute_quality_indicators,
    format_indicators_markdown,
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def test_parse_needed_entry_variants():
    assert _parse_needed_entry("src/a.py -- purpose") == "src/a.py"
    assert _parse_needed_entry("src/a.py") == "src/a.py"
    assert _parse_needed_entry("  src/a.py  ") == "src/a.py"
    assert _parse_needed_entry("") == ""
    assert _parse_needed_entry(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_coverage_no_needed_artifacts_vacuously_passes():
    plan = {
        "context": {"needed_artifacts": []},
        "design": {"artifacts": [{"filename": "src/a.py", "content": "x = 1"}]},
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan)
    assert q.coverage == 100.0
    assert "vacuously" in q.coverage_detail["note"].lower()


def test_coverage_all_shipped_non_stub():
    plan = {
        "context": {
            "needed_artifacts": [
                "src/a.py -- do a",
                "src/b.py -- do b",
            ]
        },
        "design": {
            "artifacts": [
                {"filename": "src/a.py", "content": "def a(): return 1\n"},
                {"filename": "src/b.py", "content": "def b(): return 2\n"},
            ]
        },
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan)
    assert q.coverage == 100.0
    assert q.coverage_detail["shipped"] == 2
    assert q.coverage_detail["stubbed"] == []
    assert q.coverage_detail["missing"] == []


def test_coverage_missing_file_dings_score():
    plan = {
        "context": {
            "needed_artifacts": ["src/a.py", "src/b.py"]
        },
        "design": {
            "artifacts": [{"filename": "src/a.py", "content": "def a(): return 1\n"}]
        },
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan)
    assert q.coverage == 50.0
    assert q.coverage_detail["missing"] == ["src/b.py"]


def test_coverage_notimplementederror_stub_counts_as_uncovered():
    plan = {
        "context": {"needed_artifacts": ["src/engine.py"]},
        "design": {
            "artifacts": [
                {
                    "filename": "src/engine.py",
                    "content": (
                        "def stream():\n"
                        "    # Placeholder — TODO wire up real logic\n"
                        "    raise NotImplementedError('streaming not implemented')\n"
                    ),
                }
            ]
        },
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan)
    assert q.coverage == 0.0
    assert q.coverage_detail["stubbed"] == ["src/engine.py"]


# ---------------------------------------------------------------------------
# Actionability
# ---------------------------------------------------------------------------


def test_actionability_all_phases_have_verification():
    plan = {
        "context": {"needed_artifacts": []},
        "design": {"artifacts": []},
        "roadmap": {
            "phases": [
                {"number": 1, "name": "setup", "verification_command": "pytest tests/setup"},
                {"number": 2, "name": "impl", "verification_command": "pytest tests/impl"},
            ]
        },
    }
    q = compute_quality_indicators(plan)
    assert q.actionability == 100.0
    assert q.actionability_detail["actionable"] == 2


def test_actionability_partial_verification():
    plan = {
        "context": {"needed_artifacts": []},
        "design": {"artifacts": []},
        "roadmap": {
            "phases": [
                {"number": 1, "verification_command": "pytest tests/setup"},
                {"number": 2, "verification_command": ""},
                {"number": 3, "verification_command": "TODO"},
                {"number": 4, "verification_command": "make lint"},
            ]
        },
    }
    q = compute_quality_indicators(plan)
    # 2 of 4 phases have real verification commands
    assert q.actionability == 50.0


def test_actionability_no_phases_scores_zero():
    plan = {
        "context": {"needed_artifacts": []},
        "design": {"artifacts": []},
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan)
    assert q.actionability == 0.0
    assert "no roadmap phases" in q.actionability_detail["note"].lower()


# ---------------------------------------------------------------------------
# Groundedness (no structural index → fallback to 100 with no violations)
# ---------------------------------------------------------------------------


def test_groundedness_empty_artifacts_vacuously_passes():
    plan = {
        "context": {"needed_artifacts": []},
        "design": {"artifacts": []},
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan)
    assert q.groundedness == 100.0
    assert "no artifacts" in q.groundedness_detail["note"].lower()


def test_groundedness_runs_without_crashing_on_real_artifacts():
    plan = {
        "context": {"needed_artifacts": []},
        "design": {
            "artifacts": [
                {
                    "filename": "src/a.py",
                    "content": "def a(x):\n    return x + 1\n",
                },
            ]
        },
        "roadmap": {"phases": []},
    }
    q = compute_quality_indicators(plan, structural_index="", source_dir="")
    # Without a real structural index, no violations are flagged for
    # self-contained artifacts. Score should land at 100.
    assert 0.0 <= q.groundedness <= 100.0


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_format_indicators_markdown_includes_every_row():
    q = QualityIndicators(
        coverage=50.0,
        craft=99.8,
        groundedness=100.0,
        actionability=75.0,
        coverage_detail={
            "declared": 2,
            "shipped": 1,
            "stubbed": ["engine.py"],
            "missing": [],
        },
        craft_detail={
            "artifacts": 8,
            "artifact_quality": 99.0,
            "consistency": 100.0,
            "fabrications": 0,
            "parse_failures": 0,
        },
        groundedness_detail={
            "artifacts": 8,
            "violations": 0,
            "artifacts_with_violations": 0,
            "examples": [],
        },
        actionability_detail={
            "phases": 4,
            "actionable": 3,
            "missing_verification": ["phase 2"],
        },
    )
    out = format_indicators_markdown(q)
    assert "## Quality Indicators" in out
    assert "Coverage" in out and "50.0" in out
    assert "Craft" in out and "99.8" in out
    assert "Groundedness" in out and "100.0" in out
    assert "Actionability" in out and "75.0" in out
    assert "engine.py" in out
    assert "SCORER-V2-SPEC.md" in out


def test_format_indicators_markdown_honors_detail_notes():
    q = QualityIndicators(
        coverage=100.0,
        craft=0.0,
        groundedness=100.0,
        actionability=0.0,
        coverage_detail={"note": "No declared needed_artifacts — coverage vacuously 100."},
        craft_detail={"note": "No artifacts to score."},
        groundedness_detail={"note": "No artifacts to ground."},
        actionability_detail={"note": "Plan has no roadmap phases."},
    )
    out = format_indicators_markdown(q)
    assert "vacuously" in out.lower()
    assert "no artifacts to score" in out.lower()
    assert "no roadmap phases" in out.lower()
