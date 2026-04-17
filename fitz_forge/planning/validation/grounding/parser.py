# fitz_forge/planning/validation/grounding/parser.py
"""Tree-sitter parser infrastructure for grounding checks.

Centralises parser construction so callers don't each hold a singleton.
Exposes ``parse_python(source) -> Tree | None`` with a recovery chain
(raw → dedent → class-wrap → import-split + class-wrap) matching the
shapes produced by the artifact pipeline.

The module is intentionally narrow — tree traversal utilities live next
to the callers that use them.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Parser, Tree


_parser: "Parser | None" = None


def _get_parser() -> "Parser":
    """Return a module-level Python parser, instantiated on first use."""
    global _parser
    if _parser is None:
        from tree_sitter import Language, Parser
        import tree_sitter_python

        _parser = Parser(Language(tree_sitter_python.language()))
    return _parser


def _has_error(tree: "Tree") -> bool:
    """True iff the tree contains any ERROR nodes or missing nodes."""
    return tree.root_node.has_error


def _parse_or_none(source: str) -> "Tree | None":
    """Parse ``source`` and return the tree iff it has no ERROR nodes."""
    parser = _get_parser()
    tree = parser.parse(source.encode("utf-8"))
    return None if _has_error(tree) else tree


def parse_python(content: str) -> "Tree | None":
    """Parse Python source with surgical-artifact recovery.

    Mirrors ``inference.try_parse`` exactly:
      1. Raw
      2. Dedent
      3. Class-wrap (whole content)
      4. Import-split: top-level ``import`` lines kept; indented body
         dedented and class-wrapped; imports prepended.

    Returns a tree-sitter ``Tree`` on success, ``None`` on failure.
    """
    for attempt in (
        content,
        textwrap.dedent(content),
        "class _:\n    " + content.replace("\n", "\n    "),
    ):
        tree = _parse_or_none(attempt)
        if tree is not None:
            return tree

    # Recovery 4: split top-level imports from indented body
    lines = content.split("\n")
    imports: list[str] = []
    body: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if (
            not body
            and line == stripped
            and (stripped.startswith("import ") or stripped.startswith("from "))
        ):
            imports.append(line)
        else:
            body.append(line)
    if imports and body:
        body_text = textwrap.dedent("\n".join(body))
        wrapped = (
            "\n".join(imports) + "\n\nclass _:\n    " + body_text.replace("\n", "\n    ")
        )
        tree = _parse_or_none(wrapped)
        if tree is not None:
            return tree

    return None
