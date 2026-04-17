# fitz_forge/planning/artifact/_ts_validate.py
"""Tree-sitter implementations of the ast-using checks in validate.py.

Each function is a 1:1 parity counterpart. Entry points in validate.py
route to these when ``grounding.index.get_engine() == "tree_sitter"``.

The ast-backed implementations stay in validate.py unchanged, so flipping
back to ``ast`` via ``set_engine`` keeps behaviour identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..validation.grounding._ts_inference import (
    _class_body,
    _class_name,
    _rightmost_attribute_name,
    _unwrap_decorated,
    iter_all_classes,
)
from ..validation.grounding._ts_parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node


_DATA_BASES = frozenset(
    {
        "BaseModel",
        "Enum",
        "IntEnum",
        "StrEnum",
        "Flag",
        "IntFlag",
        "TypedDict",
        "NamedTuple",
    }
)
_DATA_DECORATORS = frozenset({"dataclass", "pydantic_dataclass", "attr.s", "attrs", "define"})

_ITERATOR_TYPES = ("Iterator", "Generator", "AsyncIterator", "AsyncGenerator")


def _decorator_name(dec_node: "Node") -> str | None:
    """Return the leaf identifier of a ``decorator`` node.

    Handles ``@foo``, ``@pkg.foo``, ``@foo(arg)``, ``@pkg.foo(arg)``.
    Matches ast behaviour: ``ast.Name.id``, ``ast.Attribute.attr``,
    ``ast.Call.func.id``/``.attr``.
    """
    # decorator has a single meaningful child after the @ token
    body: Node | None = None
    for c in dec_node.children:
        if c.is_named:
            body = c
            break
    if body is None:
        return None
    if body.type == "identifier":
        return body.text.decode("utf-8")
    if body.type == "attribute":
        return _rightmost_attribute_name(body)
    if body.type == "call":
        # callee is the first named child
        callee = None
        for c in body.children:
            if c.is_named and c.type != "argument_list":
                callee = c
                break
        if callee is None:
            return None
        if callee.type == "identifier":
            return callee.text.decode("utf-8")
        if callee.type == "attribute":
            return _rightmost_attribute_name(callee)
    return None


def _class_decorators(class_def: "Node") -> list[str]:
    """Return decorator leaf names for a class node.

    The class_def we're handed may be either:
      - the bare ``class_definition``
      - the inner class after unwrapping ``decorated_definition`` —
        in which case decorators sit on the parent's children list
    """
    parent = class_def.parent
    if parent is not None and parent.type == "decorated_definition":
        out: list[str] = []
        for c in parent.children:
            if c.type == "decorator":
                name = _decorator_name(c)
                if name:
                    out.append(name)
        return out
    return []


def _is_data_class(class_def: "Node") -> bool:
    """Tree-sitter port of ``validate._is_data_class``.

    True iff the class is a Pydantic / dataclass / Enum / TypedDict /
    plain class with annotated fields. Matches the ast version's
    short-circuit order: annotated field → base → decorator.
    """
    body = _class_body(class_def)
    if body is not None:
        for stmt in body.children:
            if stmt.type != "expression_statement":
                continue
            inner = next((c for c in stmt.children if c.is_named), None)
            if inner is None or inner.type != "assignment":
                continue
            # Annotated assignment has a ``type`` child between the name and ``=``
            has_type = any(c.type == "type" for c in inner.children)
            if has_type:
                return True

    # Base classes
    args = next((c for c in class_def.children if c.type == "argument_list"), None)
    if args is not None:
        for c in args.children:
            if not c.is_named:
                continue
            name: str | None = None
            if c.type == "identifier":
                name = c.text.decode("utf-8")
            elif c.type == "attribute":
                name = _rightmost_attribute_name(c)
            if name in _DATA_BASES:
                return True

    # Decorators
    for d in _class_decorators(class_def):
        if d in _DATA_DECORATORS:
            return True

    return False


def _iter_all_functions(root: "Node"):
    """Yield every function_definition in the tree (nested and decorated).

    Matches ``ast.walk`` filtered to FunctionDef/AsyncFunctionDef.
    """
    stack: list[Node] = [root]
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if n.type == "function_definition" and n.id not in seen:
            seen.add(n.id)
            yield n
        elif n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type == "function_definition" and inner.id not in seen:
                seen.add(inner.id)
                yield inner
        stack.extend(n.children)


def check_empty_structural(content: str) -> str | None:
    """Tree-sitter equivalent of the ``_check_empty`` tree-walk branch.

    Returns None if the content has a function/method def or a
    data-model class. Returns a reason string otherwise. The parser-
    failure fallback (non-Python content) stays in validate.py.
    """
    tree = parse_python(content)
    if tree is None:
        return "__parse_failed__"  # sentinel — caller falls back to text heuristics
    root = tree.root_node

    for _fn in _iter_all_functions(root):
        return None

    for cls in iter_all_classes(root):
        if _is_data_class(cls):
            return None

    return (
        "Content has no function/method defs and no data-model class"
    )


def check_return_type(content: str) -> tuple[str, str] | None:
    """Tree-sitter port of ``validate._check_return_type``'s body.

    Returns ``(method_name, return_type_text)`` if a streaming-named
    method has a non-iterator return type. ``None`` if no violation
    (or if parsing failed).
    """
    tree = parse_python(content)
    if tree is None:
        return None
    for fn in _iter_all_functions(tree.root_node):
        # Name
        name_node = next((c for c in fn.children if c.type == "identifier"), None)
        if name_node is None:
            continue
        name = name_node.text.decode("utf-8")
        if "stream" not in name.lower():
            continue
        # Return annotation: ``type`` node between parameters and ``:``
        saw_params = False
        ret_node: Node | None = None
        for c in fn.children:
            if c.type == "parameters":
                saw_params = True
                continue
            if saw_params and c.type == "type":
                ret_node = c
                break
        if ret_node is None:
            continue
        # Unwrap the ``type`` wrapper
        named = [c for c in ret_node.children if c.is_named]
        ret_text = (named[0] if len(named) == 1 else ret_node).text.decode("utf-8")
        if not any(t in ret_text for t in _ITERATOR_TYPES):
            return name, ret_text
    return None
