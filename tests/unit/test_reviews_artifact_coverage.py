# tests/unit/test_reviews_artifact_coverage.py
"""Tests for the deterministic artifact-coverage review."""

from __future__ import annotations

from fitz_forge.planning.reviews import review_artifact_coverage
from fitz_forge.planning.reviews.artifact_coverage import (
    _artifact_filenames,
    _parse_needed_entry,
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def test_parse_needed_entry_with_purpose():
    fn, purpose = _parse_needed_entry("src/foo.py -- add bar function")
    assert fn == "src/foo.py"
    assert purpose == "add bar function"


def test_parse_needed_entry_without_purpose():
    fn, purpose = _parse_needed_entry("src/foo.py")
    assert fn == "src/foo.py"
    assert purpose == ""


def test_parse_needed_entry_strips_whitespace():
    fn, purpose = _parse_needed_entry("  src/foo.py   --   add bar  ")
    assert fn == "src/foo.py"
    assert purpose == "add bar"


def test_parse_needed_entry_empty():
    assert _parse_needed_entry("") == ("", "")
    assert _parse_needed_entry("   ") == ("", "")
    assert _parse_needed_entry(None) == ("", "")  # type: ignore[arg-type]


def test_artifact_filenames_extracts_from_dicts():
    artifacts = [
        {"filename": "a.py", "content": "x"},
        {"filename": "b.py"},
        {"filename": "  "},  # blank — ignored
        {"purpose": "no filename"},
    ]
    assert _artifact_filenames(artifacts) == {"a.py", "b.py"}


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------


def test_no_needed_artifacts_passes():
    result = review_artifact_coverage([], [{"filename": "anything.py"}])
    assert result.passed is True
    assert result.issues == []


def test_all_produced_passes():
    needed = [
        "src/a.py -- do a",
        "src/b.py -- do b",
    ]
    produced = [{"filename": "src/a.py"}, {"filename": "src/b.py"}]
    result = review_artifact_coverage(needed, produced)
    assert result.passed is True
    assert result.issues == []


# ---------------------------------------------------------------------------
# Missing files flagged
# ---------------------------------------------------------------------------


def test_missing_single_file_flagged():
    needed = [
        "src/a.py -- do a",
        "src/b.py -- do b",
    ]
    produced = [{"filename": "src/a.py"}]
    result = review_artifact_coverage(needed, produced)
    assert result.passed is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.scope == "artifact_coverage"
    assert issue.target == "src/b.py"
    assert "do b" in issue.suggestion
    assert "src/b.py" in issue.actual


def test_missing_multiple_files_each_flagged():
    needed = [
        "src/a.py",
        "src/b.py",
        "src/c.py",
    ]
    produced = [{"filename": "src/a.py"}]
    result = review_artifact_coverage(needed, produced)
    assert result.passed is False
    targets = {i.target for i in result.issues}
    assert targets == {"src/b.py", "src/c.py"}


def test_missing_without_purpose_still_flagged():
    needed = ["src/foo.py"]
    produced = []
    result = review_artifact_coverage(needed, produced)
    assert result.passed is False
    assert result.issues[0].target == "src/foo.py"
    # suggestion should not include a "with purpose" clause when purpose empty
    assert "with purpose" not in result.issues[0].suggestion


# ---------------------------------------------------------------------------
# Duplicates + tolerance
# ---------------------------------------------------------------------------


def test_duplicate_needed_entries_deduplicated():
    # Same file listed twice — only one issue if missing.
    needed = [
        "src/foo.py -- do foo",
        "src/foo.py -- also do foo",
    ]
    result = review_artifact_coverage(needed, [])
    assert len(result.issues) == 1
    assert result.issues[0].target == "src/foo.py"


def test_malformed_artifact_entries_ignored():
    needed = ["src/a.py"]
    produced = [
        "not a dict",  # string — ignored
        {"filename": None},  # bad type — ignored
        {"filename": "src/a.py"},  # real match
    ]
    result = review_artifact_coverage(needed, produced)
    assert result.passed is True


def test_empty_filename_in_needed_entry_ignored():
    needed = ["", "   ", "src/real.py"]
    produced = [{"filename": "src/real.py"}]
    result = review_artifact_coverage(needed, produced)
    assert result.passed is True
