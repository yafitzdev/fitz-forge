# tests/unit/test_ts_inference_parity.py
"""Parity between ``inference`` (ast-backed) and ``_ts_inference`` (tree-sitter).

Every function ported to tree-sitter must produce identical output to the
ast version across a representative corpus. Parameterised cases share
the same source text, which is fed through both parsers and then compared.
"""

from __future__ import annotations

import ast

import pytest

from fitz_forge.planning.validation.grounding import _ts_inference
from fitz_forge.planning.validation.grounding._ts_parser import parse_python
from fitz_forge.planning.validation.grounding.inference import extract_type_name


def _first_annotation_ast(src: str) -> ast.expr | None:
    """Pull the annotation node out of ``x: T`` source using Python ast."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            return node.annotation
    return None


def _first_annotation_ts(src: str):
    """Pull the ``type`` node out of ``x: T`` source using tree-sitter."""
    tree = parse_python(src)
    assert tree is not None, "tree-sitter should parse the source"

    def find_type(n):
        if n.type == "type":
            return n
        for c in n.children:
            r = find_type(c)
            if r is not None:
                return r
        return None

    return find_type(tree.root_node)


# (source, expected) — expected is the single string both backends must return.
EXTRACT_TYPE_NAME_CASES: list[tuple[str, str | None]] = [
    ("x: ChatRequest", "ChatRequest"),
    ("x: Optional[ChatRequest]", "ChatRequest"),
    ("x: list[ChatRequest]", "list"),           # container, not element
    ("x: dict[str, int]", "dict"),              # container
    ("x: set[Foo]", "set"),
    ("x: tuple[int, str]", "tuple"),
    ("x: fitz.ChatRequest", "ChatRequest"),     # dotted → rightmost
    # Iterator/AsyncIterator are NOT in _CONTAINER_TYPES, so both backends
    # return the inner type (documented quirk — the ast docstring is misleading).
    ("x: Iterator[str]", "str"),
    ("x: AsyncIterator[Response]", "Response"),
    ("x: Union[Foo, Bar]", "Foo"),              # first inner
    ("x: Mapping[str, int]", "Mapping"),        # container
    ("x: 'ChatRequest'", "ChatRequest"),        # forward-ref string
    ('x: "ChatRequest"', "ChatRequest"),
]


@pytest.mark.parametrize("src,expected", EXTRACT_TYPE_NAME_CASES)
def test_extract_type_name_parity(src: str, expected: str | None) -> None:
    ast_ann = _first_annotation_ast(src)
    ts_ann = _first_annotation_ts(src)
    assert ast_ann is not None
    assert ts_ann is not None

    ast_result = extract_type_name(ast_ann)
    ts_result = _ts_inference.extract_type_name(ts_ann)
    assert ast_result == expected, f"ast: {ast_result!r} != {expected!r}"
    assert ts_result == expected, f"ts: {ts_result!r} != {expected!r}"
    assert ast_result == ts_result, "backends must agree"


def test_extract_type_name_none_input() -> None:
    assert _ts_inference.extract_type_name(None) is None
    assert extract_type_name(None) is None
