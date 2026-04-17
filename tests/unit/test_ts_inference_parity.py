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


# ---------------------------------------------------------------------------
# unparse_annotation
# ---------------------------------------------------------------------------


UNPARSE_CASES = [
    ("x: ChatRequest", "ChatRequest"),
    ("x: list[str]", "list[str]"),
    ("x: Optional[int]", "Optional[int]"),
    ("x: fitz.ChatRequest", "fitz.ChatRequest"),
    ("x: 'Forward'", "'Forward'"),
]


@pytest.mark.parametrize("src,expected", UNPARSE_CASES)
def test_unparse_annotation_parity(src: str, expected: str) -> None:
    from fitz_forge.planning.validation.grounding.inference import unparse_annotation

    ast_ann = _first_annotation_ast(src)
    ts_ann = _first_annotation_ts(src)
    assert ast_ann is not None and ts_ann is not None
    assert unparse_annotation(ast_ann) == expected
    assert _ts_inference.unparse_annotation(ts_ann) == expected


# ---------------------------------------------------------------------------
# class_name_of_expr
# ---------------------------------------------------------------------------


def _first_return_value_ast(src: str):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Return) and n.value is not None:
            return n.value
    return None


def _first_return_value_ts(src: str):
    tree = parse_python(src)
    assert tree is not None

    def find(n):
        if n.type == "return_statement":
            for c in n.children:
                if c.is_named:
                    return c
        for c in n.children:
            r = find(c)
            if r is not None:
                return r
        return None

    return find(tree.root_node)


CLASS_NAME_OF_EXPR_CASES: list[tuple[str, str | None]] = [
    ("def f():\n    return ClassName()\n", "ClassName"),
    ("def f():\n    return module.ClassName()\n", "ClassName"),
    ("def f():\n    return ClassName.from_x()\n", "ClassName"),
    ("def f():\n    return classname()\n", None),                 # lowercase
    ("def f():\n    return module.classname()\n", None),          # lowercase attr
    ("def f():\n    return obj.method()\n", None),                # both lowercase
    ("def f():\n    return 5\n", None),                           # not a call
    ("def f():\n    return self.foo.bar.baz()\n", None),           # 3+ idents, nothing matches
]


@pytest.mark.parametrize("src,expected", CLASS_NAME_OF_EXPR_CASES)
def test_class_name_of_expr_parity(src: str, expected: str | None) -> None:
    from fitz_forge.planning.validation.grounding.inference import class_name_of_expr

    ast_val = _first_return_value_ast(src)
    ts_val = _first_return_value_ts(src)
    assert ast_val is not None and ts_val is not None
    assert class_name_of_expr(ast_val) == expected
    assert _ts_inference.class_name_of_expr(ts_val) == expected


# ---------------------------------------------------------------------------
# Return-type inference (body / yields / docstring / annotation)
# ---------------------------------------------------------------------------


def _first_function_ast(src: str):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return n
    return None


def _first_function_ts(src: str):
    tree = parse_python(src)
    assert tree is not None

    def find(n):
        if n.type == "function_definition":
            return n
        for c in n.children:
            r = find(c)
            if r is not None:
                return r
        return None

    return find(tree.root_node)


INFER_RETURN_CASES: list[tuple[str, set[str], str | None]] = [
    # Explicit annotation wins
    ("def f() -> ChatRequest: return ChatRequest()\n", set(), "ChatRequest"),
    # Body inference
    ("def f():\n    return ChatRequest()\n", set(), "ChatRequest"),
    # Ambiguous body -> None
    ("def f():\n    if x:\n        return ChatRequest()\n    return Foo()\n", set(), None),
    # Yield -> Iterator
    ("def f():\n    yield 1\n", set(), "Iterator"),
    # Async yield -> AsyncIterator
    ("async def f():\n    yield 1\n", set(), "AsyncIterator"),
    # Docstring returns, gated on known_classes
    (
        'def f():\n    """Do thing.\n\n    Returns:\n        ChatRequest: ok.\n    """\n    pass\n',
        {"ChatRequest"},
        "ChatRequest",
    ),
    # Docstring returns but class unknown -> None
    (
        'def f():\n    """Do thing.\n\n    Returns:\n        Unknown: ok.\n    """\n    pass\n',
        {"ChatRequest"},
        None,
    ),
    # Nested function's return DOES leak into outer (ast-version quirk
    # that both backends preserve: ast.walk doesn't honour the "skip
    # nested function" guard). Documents the shared behaviour.
    (
        "def outer():\n    def inner():\n        return Foo()\n    return Bar()\n",
        set(),
        None,  # ambiguous: {Foo, Bar}
    ),
]


@pytest.mark.parametrize("src,known,expected", INFER_RETURN_CASES)
def test_infer_return_type_parity(src: str, known: set[str], expected: str | None) -> None:
    from fitz_forge.planning.validation.grounding.inference import infer_return_type

    ast_fn = _first_function_ast(src)
    ts_fn = _first_function_ts(src)
    assert ast_fn is not None and ts_fn is not None
    assert infer_return_type(ast_fn, known) == expected
    assert _ts_inference.infer_return_type(ts_fn, known) == expected


# ---------------------------------------------------------------------------
# extract_class_fields
# ---------------------------------------------------------------------------


def _first_class_ast(src: str) -> ast.ClassDef | None:
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            return n
    return None


def _first_class_ts(src: str):
    tree = parse_python(src)
    assert tree is not None

    def find(n):
        if n.type == "class_definition":
            return n
        for c in n.children:
            r = find(c)
            if r is not None:
                return r
        return None

    return find(tree.root_node)


CLASS_FIELDS_CASES: list[tuple[str, dict[str, str]]] = [
    (
        "class Req:\n    name: str\n    count: int = 0\n    tags: list[str] = []\n",
        {"name": "str", "count": "int", "tags": "list"},
    ),
    (
        "class Empty:\n    pass\n",
        {},
    ),
    (
        "class HasMethod:\n    x: int\n    def foo(self): return 1\n",
        {"x": "int"},
    ),
    (
        "class HasClassVar:\n    from typing import ClassVar\n    shared: ClassVar[int] = 0\n    each: int = 1\n",
        {"each": "int"},
    ),
]


@pytest.mark.parametrize("src,expected", CLASS_FIELDS_CASES)
def test_extract_class_fields_parity(src: str, expected: dict[str, str]) -> None:
    from fitz_forge.planning.validation.grounding.inference import extract_class_fields

    ast_cls = _first_class_ast(src)
    ts_cls = _first_class_ts(src)
    assert ast_cls is not None and ts_cls is not None
    ast_fields = extract_class_fields(ast_cls)
    ts_fields = _ts_inference.extract_class_fields(ts_cls)
    assert ast_fields == expected
    assert ts_fields == expected


# ---------------------------------------------------------------------------
# extract_init_self_attrs
# ---------------------------------------------------------------------------


INIT_ATTRS_CASES: list[tuple[str, set[str] | None, dict[str, str]]] = [
    # self._x = param  (from annotated param)
    (
        "class F:\n    def __init__(self, svc: Service):\n        self._svc = svc\n",
        None,
        {"_svc": "Service"},
    ),
    # self._x = ClassName(...)
    (
        "class F:\n    def __init__(self):\n        self._store = Store(cfg)\n",
        None,
        {"_store": "Store"},
    ),
    # self._x: T = val
    (
        "class F:\n    def __init__(self):\n        self._count: int = 0\n",
        None,
        {"_count": "int"},
    ),
    # Class-level _x: T declaration
    (
        "class F:\n    _name: str\n    def __init__(self):\n        pass\n",
        None,
        {"_name": "str"},
    ),
    # known_classes filter on construction
    (
        "class F:\n    def __init__(self):\n        self._thing = Unknown()\n",
        {"Store"},
        {},
    ),
    # No __init__ -> only class-level anns
    (
        "class F:\n    _x: int\n",
        None,
        {"_x": "int"},
    ),
]


@pytest.mark.parametrize("src,known,expected", INIT_ATTRS_CASES)
def test_extract_init_self_attrs_parity(
    src: str, known: set[str] | None, expected: dict[str, str]
) -> None:
    from fitz_forge.planning.validation.grounding.inference import extract_init_self_attrs

    ast_cls = _first_class_ast(src)
    ts_cls = _first_class_ts(src)
    assert ast_cls is not None and ts_cls is not None
    ast_attrs = extract_init_self_attrs(ast_cls, known)
    ts_attrs = _ts_inference.extract_init_self_attrs(ts_cls, known)
    assert ast_attrs == expected
    assert ts_attrs == expected
