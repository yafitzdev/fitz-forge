# fitz_forge/planning/validation/grounding/_ts_inference.py
"""Tree-sitter implementations of grounding/inference primitives.

Parallel to ``inference.py``. Each function here has a one-to-one parity
counterpart in ``inference.py`` and is validated by ``tests/unit/
test_ts_inference_parity.py``. Once every function in ``inference.py``
has a green parity port, callers flip over and the ast path is deleted.

Tree-sitter node shapes used here (Python grammar):
    type              wraps an annotation ("x: T" → T is a `type` node)
    identifier        bare name
    attribute         dotted path, rightmost identifier is `.attr`
    generic_type      subscript form, outer identifier + type_parameter
    type_parameter    the `[...]` block, children are comma-separated `type`s
    string            forward reference like "ClassName"
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .inference import _CONTAINER_TYPES

if TYPE_CHECKING:
    from tree_sitter import Node


def _first_identifier_text(node: "Node") -> str | None:
    for c in node.children:
        if c.type == "identifier":
            return c.text.decode("utf-8")
    return None


def _rightmost_attribute_name(node: "Node") -> str | None:
    """For an ``attribute`` node ``a.b.c``, return ``c``."""
    last: str | None = None
    for c in node.children:
        if c.type == "identifier":
            last = c.text.decode("utf-8")
    return last


def extract_type_name(node: "Node | None") -> str | None:
    """Tree-sitter port of ``inference.extract_type_name``.

    Matches the ast-backed function's semantics exactly:
        ``ChatRequest``          → ``ChatRequest``
        ``Optional[ChatRequest]`` → ``ChatRequest``
        ``list[ChatRequest]``    → ``list``  (container — NOT element)
        ``fitz.ChatRequest``     → ``ChatRequest``
        ``Iterator[str]``        → ``Iterator``
        ``"ChatRequest"``        → ``ChatRequest`` (forward-ref string)
    """
    if node is None:
        return None

    # Unwrap the ``type`` wrapper tree-sitter places around an annotation
    # expression. Walk down until we hit something concrete.
    if node.type == "type":
        named = [c for c in node.children if c.is_named]
        if len(named) == 1:
            return extract_type_name(named[0])
        return None

    if node.type == "identifier":
        return node.text.decode("utf-8")

    if node.type == "attribute":
        return _rightmost_attribute_name(node)

    if node.type == "generic_type":
        outer: str | None = None
        params: "Node | None" = None
        for c in node.children:
            if c.type == "identifier":
                outer = c.text.decode("utf-8")
            elif c.type == "attribute":
                outer = _rightmost_attribute_name(c)
            elif c.type == "type_parameter":
                params = c
        if outer in _CONTAINER_TYPES:
            return outer
        if params is not None:
            inner_types = [c for c in params.children if c.type == "type"]
            if inner_types:
                return extract_type_name(inner_types[0])
        return outer

    if node.type == "string":
        txt = node.text.decode("utf-8").strip()
        # strip one pair of surrounding quotes (single, double, or triple)
        for q in ('"""', "'''", '"', "'"):
            if txt.startswith(q) and txt.endswith(q) and len(txt) >= 2 * len(q):
                txt = txt[len(q) : -len(q)]
                break
        m = re.match(r"^[A-Za-z_][A-Za-z_0-9]*", txt.strip())
        return m.group(0) if m else None

    return None
