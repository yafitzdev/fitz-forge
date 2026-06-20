# tests/unit/test_synthesis_rubric_hints.py
"""Tests for the rubric-injection mechanism.

The pipeline accepts an optional ``rubric_hints`` string at job creation
time and threads it into the synthesis reasoning prompt. This surfaces
domain-level quality criteria the model cannot infer from the task
prompt or codebase alone — e.g. "preserve pre-rerank score" on a
ranking task, or "never buffer the full response" on a streaming task.
Codebase/language agnostic; the content is free-form markdown.
"""

from __future__ import annotations

from pathlib import Path

from fitz_forge.planning.pipeline.stages.synthesis import _format_rubric_hints
from fitz_forge.planning.prompts import load_prompt


# ---------------------------------------------------------------------------
# _format_rubric_hints — formatter
# ---------------------------------------------------------------------------


def test_format_rubric_hints_empty_input_returns_empty_string():
    assert _format_rubric_hints({}) == ""
    assert _format_rubric_hints({"_rubric_hints": None}) == ""
    assert _format_rubric_hints({"_rubric_hints": ""}) == ""
    assert _format_rubric_hints({"_rubric_hints": "   \n\n  "}) == ""


def test_format_rubric_hints_present_returns_block_with_header():
    out = _format_rubric_hints({"_rubric_hints": "- preserve pre-rerank score\n- record breakdown"})
    assert "## Quality Criteria" in out
    assert "preserve pre-rerank score" in out
    assert "record breakdown" in out


def test_format_rubric_hints_preserves_user_markdown():
    """Free-form markdown from the user passes through untouched."""
    hints = "## Rule 1\n- do X\n\n## Rule 2\n- do Y (because Z)"
    out = _format_rubric_hints({"_rubric_hints": hints})
    assert "Rule 1" in out
    assert "do X" in out
    assert "do Y (because Z)" in out


# ---------------------------------------------------------------------------
# synthesis prompt consumes the placeholder
# ---------------------------------------------------------------------------


def test_synthesis_prompt_formats_with_rubric_placeholder():
    tmpl = load_prompt("synthesis")
    formatted = tmpl.format(
        task_description="t",
        resolved_decisions="d",
        call_graph="c",
        gathered_context="g",
        rubric_hints="## Quality Criteria\n\n- rule X\n",
    )
    assert "rule X" in formatted
    # Rubric block should sit before the main Instructions section so
    # the reasoning model sees it while forming its approach.
    rubric_pos = formatted.index("rule X")
    instr_pos = formatted.index("## Instructions")
    assert rubric_pos < instr_pos


def test_synthesis_prompt_format_with_empty_rubric_still_valid():
    """Callers passing empty rubric must still get a valid prompt."""
    tmpl = load_prompt("synthesis")
    formatted = tmpl.format(
        task_description="t",
        resolved_decisions="d",
        call_graph="c",
        gathered_context="g",
        rubric_hints="",
    )
    # No stray error or header when rubric is absent.
    assert "Quality Criteria" not in formatted
    assert "## Instructions" in formatted


# ---------------------------------------------------------------------------
# Benchmark runner — per-task rubric.md loader
# ---------------------------------------------------------------------------


def test_load_rubric_hints_missing_file_returns_none(tmp_path):
    from benchmarks.plan_factory import _load_rubric_hints

    ctx = tmp_path / "task" / "ideal_context.json"
    ctx.parent.mkdir()
    ctx.write_text("{}")
    assert _load_rubric_hints(ctx) is None


def test_load_rubric_hints_reads_sibling_file(tmp_path):
    from benchmarks.plan_factory import _load_rubric_hints

    task = tmp_path / "task"
    task.mkdir()
    (task / "ideal_context.json").write_text("{}")
    (task / "rubric.md").write_text("# Quality criteria\n- do X\n")

    out = _load_rubric_hints(task / "ideal_context.json")
    assert out is not None
    assert "Quality criteria" in out
    assert "do X" in out


def test_load_rubric_hints_empty_file_returns_none(tmp_path):
    """Empty rubric.md is treated the same as a missing one."""
    from benchmarks.plan_factory import _load_rubric_hints

    task = tmp_path / "task"
    task.mkdir()
    (task / "ideal_context.json").write_text("{}")
    (task / "rubric.md").write_text("   \n\n")
    assert _load_rubric_hints(task / "ideal_context.json") is None


# ---------------------------------------------------------------------------
# End-to-end: shipped ranking_explanation rubric loads and contains
# the taxonomy's A1/K1/RR1 requirements
# ---------------------------------------------------------------------------


def test_shipped_ranking_rubric_captures_a1_requirements():
    """The production rubric.md must mention the A1-level signal names
    so the synthesis prompt surfaces them to the reasoning model.
    Regression guard: if someone rewrites the rubric and drops these
    anchors, the benchmark will silently fall back toward A2."""
    rubric = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "challenges"
        / "ranking_explanation"
        / "rubric.md"
    )
    assert rubric.is_file(), "ranking_explanation rubric should ship with the repo"
    text = rubric.read_text(encoding="utf-8")
    # K1-level anchors
    assert "base_score" in text
    assert "pre_rerank_score" in text
    assert "rerank_score" in text
    assert "retrieval_method" in text
    # RR1-level anchor (overwrite warning)
    assert "Overwriting" in text or "overwriting" in text
