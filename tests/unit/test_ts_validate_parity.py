# tests/unit/test_ts_validate_parity.py
"""Parity between ast-backed and tree-sitter-backed validate.py checks.

Each test runs both engines against the same artifact content and
confirms they return the same ArtifactError shape (check/message/
suggestion).
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.artifact.context import ArtifactContext
from fitz_forge.planning.artifact.validate import _check_empty, _check_return_type
from fitz_forge.planning.validation.grounding import index as _index_mod


@pytest.fixture
def _reset_engine():
    original = _index_mod.get_engine()
    yield
    _index_mod.set_engine(original)


CHECK_EMPTY_OK_CASES = [
    # Has a function
    "def foo():\n    return 1\n",
    # Has a method inside a class
    "class Foo:\n    def method(self):\n        return 1\n",
    # Data class: pydantic
    "from pydantic import BaseModel\n\nclass Req(BaseModel):\n    name: str\n",
    # Data class: dataclass decorator
    "from dataclasses import dataclass\n\n@dataclass\nclass Req:\n    name: str\n",
    # Data class: Enum
    "from enum import Enum\n\nclass State(Enum):\n    A = 1\n    B = 2\n",
    # Plain class with annotated fields
    "class Simple:\n    name: str\n    count: int = 0\n",
]

CHECK_EMPTY_FAIL_CASES = [
    # Class with no fields and no methods -> empty
    "class Empty:\n    pass\n",
    # Only module-level statement, no def
    "x = 1\ny = 2\n",
]


@pytest.mark.parametrize("src", CHECK_EMPTY_OK_CASES)
def test_check_empty_ok_parity(src: str, _reset_engine) -> None:
    _index_mod.set_engine("ast")
    ast_err = _check_empty(src)
    _index_mod.set_engine("tree_sitter")
    ts_err = _check_empty(src)
    assert ast_err is None, f"ast should accept: {src!r}"
    assert ts_err is None, f"ts should accept: {src!r}"


@pytest.mark.parametrize("src", CHECK_EMPTY_FAIL_CASES)
def test_check_empty_fail_parity(src: str, _reset_engine) -> None:
    _index_mod.set_engine("ast")
    ast_err = _check_empty(src)
    _index_mod.set_engine("tree_sitter")
    ts_err = _check_empty(src)
    assert ast_err is not None, f"ast should reject: {src!r}"
    assert ts_err is not None, f"ts should reject: {src!r}"
    assert ast_err.check == ts_err.check == "empty"


def test_check_return_type_streaming_correct(_reset_engine) -> None:
    """Valid streaming method with Iterator return type — both pass."""
    src = (
        "from typing import Iterator\n"
        "\n"
        "def stream_tokens(self) -> Iterator[str]:\n"
        "    yield 'a'\n"
    )
    ctx = ArtifactContext(
        filename="engine.py",
        purpose="streaming token-by-token",
        structural_index="",
    )
    _index_mod.set_engine("ast")
    assert _check_return_type(src, ctx) is None
    _index_mod.set_engine("tree_sitter")
    assert _check_return_type(src, ctx) is None


def test_check_return_type_streaming_wrong(_reset_engine) -> None:
    """Streaming file, method named stream, non-iterator return — both flag."""
    src = (
        "def stream_tokens(self) -> str:\n"
        "    return 'hello'\n"
    )
    ctx = ArtifactContext(
        filename="engine.py",
        purpose="streaming token-by-token",
        structural_index="",
    )
    _index_mod.set_engine("ast")
    ast_err = _check_return_type(src, ctx)
    _index_mod.set_engine("tree_sitter")
    ts_err = _check_return_type(src, ctx)
    assert ast_err is not None and ts_err is not None
    assert ast_err.check == ts_err.check == "return_type"
    # Both messages reference the same method name and return type.
    assert "stream_tokens" in ast_err.message
    assert "stream_tokens" in ts_err.message
    assert "str" in ast_err.message
    assert "str" in ts_err.message


def test_check_return_type_non_streaming_file_skipped(_reset_engine) -> None:
    """Non-streaming filename — both engines skip the check."""
    src = "def stream_tokens(self) -> str:\n    return 'hello'\n"
    ctx = ArtifactContext(
        filename="handlers.py",
        purpose="streaming token-by-token",
        structural_index="",
    )
    _index_mod.set_engine("ast")
    assert _check_return_type(src, ctx) is None
    _index_mod.set_engine("tree_sitter")
    assert _check_return_type(src, ctx) is None
