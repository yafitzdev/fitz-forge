# tests/unit/test_ts_parser.py
"""Parity tests between ast-based ``try_parse`` and tree-sitter ``parse_python``.

Both must agree on which snippets parse and which do not. Tree-sitter's
error-recovery is lenient (it produces ERROR nodes instead of raising),
so we gate acceptance on ``tree.root_node.has_error`` — the snippet only
"parses" if the tree is fully error-free after all recovery steps, same
semantics as ``ast.parse`` raising SyntaxError.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.validation.grounding._ts_parser import parse_python
from fitz_forge.planning.validation.grounding.inference import try_parse


# Snippets that must parse under both backends.
PARSE_OK_CASES = [
    # Raw module
    "x = 1\n",
    "def foo(x: int) -> str:\n    return str(x)\n",
    "class Foo:\n    def bar(self) -> int:\n        return 1\n",
    # Needs dedent recovery
    "    def indented(self):\n        return 1\n",
    # Needs class-wrap recovery (body-only)
    "def method(self, x):\n    self._x = x\n    return x\n",
    # Needs import-split + class-wrap (top-level import + indented body)
    (
        "import os\n"
        "from typing import Any\n"
        "def method(self, x: Any) -> Any:\n"
        "    return os.path.join(self._base, x)\n"
    ),
    # async function
    "async def foo():\n    async for x in stream():\n        yield x\n",
    # Decorated function
    "@cached\ndef foo(x):\n    return x\n",
    # Pydantic/dataclass shape
    "class Req:\n    name: str\n    count: int = 0\n",
]

# Snippets neither backend should accept (post-recovery).
PARSE_FAIL_CASES = [
    "def foo(:\n    return",        # unterminated signature
    "class ):\n    pass",             # malformed class header
    "def foo(x) return x",            # missing colon
]


@pytest.mark.parametrize("src", PARSE_OK_CASES)
def test_both_backends_accept(src: str) -> None:
    ast_tree = try_parse(src)
    ts_tree = parse_python(src)
    assert ast_tree is not None, "ast backend should accept"
    assert ts_tree is not None, "tree-sitter backend should accept"


@pytest.mark.parametrize("src", PARSE_FAIL_CASES)
def test_both_backends_reject(src: str) -> None:
    ast_tree = try_parse(src)
    ts_tree = parse_python(src)
    assert ast_tree is None, "ast backend should reject"
    assert ts_tree is None, "tree-sitter backend should reject"


def test_ts_parse_returns_tree_root_named_module() -> None:
    tree = parse_python("x = 1\n")
    assert tree is not None
    assert tree.root_node.type == "module"
