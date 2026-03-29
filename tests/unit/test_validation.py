# tests/unit/test_validation.py
"""
Tests for validation/sanitize.py: input sanitization and path validation.

Covers sanitize_description, sanitize_job_id, sanitize_project_path,
and sanitize_agent_path.
"""

import pytest

from fastmcp.exceptions import ToolError

from fitz_graveyard.validation.sanitize import (
    sanitize_agent_path,
    sanitize_description,
    sanitize_job_id,
    sanitize_project_path,
)


# ---------------------------------------------------------------------------
# sanitize_description
# ---------------------------------------------------------------------------


class TestSanitizeDescription:
    """Tests for description validation."""

    def test_normal_text(self):
        """Normal text passes through stripped."""
        result = sanitize_description("Build a REST API")
        assert result == "Build a REST API"

    def test_strips_whitespace(self):
        """Leading and trailing whitespace stripped."""
        result = sanitize_description("  Build a thing  ")
        assert result == "Build a thing"

    def test_empty_raises(self):
        """Empty string raises ToolError."""
        with pytest.raises(ToolError, match="(?i)empty"):
            sanitize_description("")

    def test_whitespace_only_raises(self):
        """Whitespace-only string raises ToolError."""
        with pytest.raises(ToolError, match="(?i)empty"):
            sanitize_description("   \t\n  ")

    def test_truncates_long_text(self):
        """Text exceeding max_length is truncated."""
        long_text = "A" * 6000
        result = sanitize_description(long_text)
        assert len(result) == 5000

    def test_custom_max_length(self):
        """Custom max_length is respected."""
        result = sanitize_description("A" * 200, max_length=100)
        assert len(result) == 100

    def test_exactly_max_length(self):
        """Text at exactly max_length is not truncated."""
        text = "B" * 5000
        result = sanitize_description(text)
        assert len(result) == 5000

    def test_unicode_preserved(self):
        """Unicode characters are preserved."""
        result = sanitize_description("Build a widget with umlauts: ae oe ue")
        assert "umlauts" in result

    def test_newlines_preserved(self):
        """Newlines within the text are preserved (only leading/trailing stripped)."""
        result = sanitize_description("Line 1\nLine 2\nLine 3")
        assert "\n" in result
        assert result == "Line 1\nLine 2\nLine 3"


# ---------------------------------------------------------------------------
# sanitize_job_id
# ---------------------------------------------------------------------------


class TestSanitizeJobId:
    """Tests for job ID validation."""

    def test_valid_hex_id(self):
        """Standard 12-char hex ID passes."""
        result = sanitize_job_id("abcdef123456")
        assert result == "abcdef123456"

    def test_valid_uuid_format(self):
        """UUID-style ID with hyphens passes."""
        result = sanitize_job_id("abc-def-1234-5678")
        assert result == "abc-def-1234-5678"

    def test_valid_8_char_minimum(self):
        """8-character minimum ID passes."""
        result = sanitize_job_id("abcd1234")
        assert result == "abcd1234"

    def test_valid_64_char_maximum(self):
        """64-character maximum ID passes."""
        long_id = "a" * 64
        result = sanitize_job_id(long_id)
        assert result == long_id

    def test_too_short_raises(self):
        """ID shorter than 8 characters raises ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("abc")

    def test_too_long_raises(self):
        """ID longer than 64 characters raises ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("a" * 65)

    def test_empty_raises(self):
        """Empty string raises ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("")

    def test_special_chars_raise(self):
        """Special characters raise ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("abc!@#$%^&*()")

    def test_spaces_raise(self):
        """Spaces in ID raise ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("abc def 1234")

    def test_underscores_raise(self):
        """Underscores raise ToolError (only hyphens allowed)."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("abc_def_1234")

    def test_dots_raise(self):
        """Dots raise ToolError."""
        with pytest.raises(ToolError, match="(?i)invalid"):
            sanitize_job_id("abc.def.1234")

    def test_uppercase_allowed(self):
        """Uppercase letters are valid."""
        result = sanitize_job_id("ABCDEF123456")
        assert result == "ABCDEF123456"

    def test_mixed_case_and_hyphens(self):
        """Mixed case with hyphens is valid."""
        result = sanitize_job_id("AbCdEf-123456")
        assert result == "AbCdEf-123456"


# ---------------------------------------------------------------------------
# sanitize_project_path
# ---------------------------------------------------------------------------


class TestSanitizeProjectPath:
    """Tests for project path validation."""

    def test_valid_directory(self, tmp_path):
        """Existing directory resolves correctly."""
        result = sanitize_project_path(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_nonexistent_raises(self):
        """Nonexistent path raises ToolError."""
        with pytest.raises(ToolError, match="(?i)does not exist"):
            sanitize_project_path("/nonexistent/path/xyz")

    def test_file_not_directory_raises(self, tmp_path):
        """Existing file (not directory) raises ToolError."""
        f = tmp_path / "notadir.txt"
        f.write_text("hello")

        with pytest.raises(ToolError, match="(?i)not a directory"):
            sanitize_project_path(str(f))

    def test_resolves_to_absolute(self, tmp_path, monkeypatch):
        """Relative path resolves to absolute."""
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()

        result = sanitize_project_path("subdir")
        assert result.is_absolute()
        assert result == sub.resolve()

    def test_returns_path_object(self, tmp_path):
        """Return type is a Path object."""
        from pathlib import Path
        result = sanitize_project_path(str(tmp_path))
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# sanitize_agent_path
# ---------------------------------------------------------------------------


class TestSanitizeAgentPath:
    """Tests for agent path confinement validation."""

    def test_valid_path_inside_root(self, tmp_path):
        """Path inside root_dir resolves correctly."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")

        result = sanitize_agent_path("src/main.py", str(tmp_path))
        assert result == (tmp_path / "src" / "main.py").resolve()

    def test_traversal_outside_root_raises(self, tmp_path):
        """Path traversal outside root raises ValueError."""
        with pytest.raises(ValueError, match="(?i)outside"):
            sanitize_agent_path("../../etc/passwd", str(tmp_path))

    def test_nonexistent_file_raises(self, tmp_path):
        """Nonexistent file raises ValueError."""
        with pytest.raises(ValueError, match="(?i)does not exist"):
            sanitize_agent_path("nonexistent.py", str(tmp_path))

    def test_absolute_path_outside_root_raises(self, tmp_path):
        """Absolute path outside root raises ValueError (after joining)."""
        # On Windows, an absolute path like /tmp/other won't be outside
        # when joined with root, but ../../../ traversals will.
        with pytest.raises(ValueError, match="(?i)outside|does not exist"):
            sanitize_agent_path("../../../tmp/sneaky.py", str(tmp_path))

    def test_root_itself_is_valid(self, tmp_path):
        """The root directory itself (empty relative path mapped to root) is valid."""
        result = sanitize_agent_path(".", str(tmp_path))
        assert result == tmp_path.resolve()
