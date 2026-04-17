# tests/unit/test_parser.py
"""Tree-sitter parser tests: recovery chain + error detection.

``parse_python`` must accept well-formed snippets (including surgical
artifact shapes needing dedent / class-wrap / import-split recovery) and
reject snippets that stay malformed after every recovery step.
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.validation.grounding.parser import parse_python


# Snippets that must parse.
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

# Snippets the parser should reject post-recovery.
PARSE_FAIL_CASES = [
    "def foo(:\n    return",        # unterminated signature
    "class ):\n    pass",             # malformed class header
    "def foo(x) return x",            # missing colon
]


@pytest.mark.parametrize("src", PARSE_OK_CASES)
def test_parser_accepts(src: str) -> None:
    assert parse_python(src) is not None


@pytest.mark.parametrize("src", PARSE_FAIL_CASES)
def test_parser_rejects(src: str) -> None:
    assert parse_python(src) is None


def test_parse_returns_tree_root_named_module() -> None:
    tree = parse_python("x = 1\n")
    assert tree is not None
    assert tree.root_node.type == "module"
