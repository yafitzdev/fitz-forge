# tests/unit/test_ts_closure_parity.py
"""Parity between ast and tree-sitter closure implementations.

The ast path still lives in ``closure.py``; the tree-sitter port lives
in ``_ts_closure``. Each test runs the same artifact through both
paths and asserts the emitted References / Provides are equivalent.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from fitz_forge.planning.artifact.closure import (
    extract_provides,
    extract_references,
)
from fitz_forge.planning.validation.grounding import index as _index_mod
from fitz_forge.planning.validation.grounding.index import StructuralIndexLookup


@pytest.fixture
def _reset_engine():
    original = _index_mod.get_engine()
    yield
    _index_mod.set_engine(original)


def _dump_refs(refs):
    return sorted([asdict(r) for r in refs], key=lambda r: (r["line"], r["context"], r["usage"]))


def _dump_provides(d):
    out = {}
    for k, v in d.items():
        sig = None if v is None else asdict(v)
        if sig is not None:
            sig["params"] = list(sig["params"])
        out[(k.owner, k.name, k.kind)] = sig
    return out


def _run_both(fn, *args, _reset_engine):
    _index_mod.set_engine("ast")
    a = fn(*args)
    _index_mod.set_engine("tree_sitter")
    t = fn(*args)
    return a, t


# ---------------------------------------------------------------------------
# extract_provides
# ---------------------------------------------------------------------------


PROVIDES_CASES = [
    # Top-level function
    "def foo(x, y) -> int:\n    return x + y\n",
    # Async function with yield
    "async def bar(self, q: str) -> 'Response':\n    yield q\n",
    # Class with method + annotated field
    "class Req:\n    name: str\n    def handle(self, q: str) -> int:\n        return 1\n",
    # Surgical-style indented method (no class wrapper)
    "    def handle(self, q: str) -> 'Response':\n        return Response()\n",
    # Decorated function
    "@cached\ndef helper(x) -> int:\n    return x\n",
]


@pytest.mark.parametrize("src", PROVIDES_CASES)
def test_extract_provides_parity(src, _reset_engine):
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(extract_provides, src, "svc.py", lookup, _reset_engine=_reset_engine)
    assert _dump_provides(a) == _dump_provides(t)


# ---------------------------------------------------------------------------
# extract_references
# ---------------------------------------------------------------------------


def test_references_imports(_reset_engine):
    src = (
        "from my_pkg.schemas import Thing\n"
        "from typing import Optional\n"      # stdlib — skipped
        "from my_pkg.services import svc as s\n"
    )
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(
        extract_references, src, "x.py", lookup, None, None,
        _reset_engine=_reset_engine,
    )
    # Both backends emit the same import references (typing is stdlib)
    a_dump = _dump_refs(a)
    t_dump = _dump_refs(t)
    assert a_dump == t_dump
    # Sanity: we did emit something — typing was dropped, two remain
    import_contexts = {r["context"] for r in a_dump if r["usage"] == "import"}
    assert "from my_pkg.schemas import Thing" in import_contexts


def test_references_class_construction(_reset_engine):
    src = (
        "def foo():\n"
        "    r = MyClass()\n"
        "    return r\n"
    )
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(
        extract_references, src, "x.py", lookup, None, None,
        _reset_engine=_reset_engine,
    )
    assert _dump_refs(a) == _dump_refs(t)
    # Should emit a class reference for MyClass
    a_dump = _dump_refs(a)
    classes = [r for r in a_dump if r["ref"]["kind"] == "class"]
    assert any(c["ref"]["owner"] == "MyClass" for c in classes)


def test_references_typed_method_call(_reset_engine):
    # obj: Foo → obj.method() emits method ref on Foo
    src = (
        "def f(obj: Foo) -> None:\n"
        "    obj.method()\n"
    )
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(
        extract_references, src, "x.py", lookup, None, None,
        _reset_engine=_reset_engine,
    )
    assert _dump_refs(a) == _dump_refs(t)


def test_references_annotation_class(_reset_engine):
    # Fabricated class in an annotation
    src = (
        "def f(x: FabricatedThing) -> None:\n"
        "    pass\n"
    )
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(
        extract_references, src, "x.py", lookup, None, None,
        _reset_engine=_reset_engine,
    )
    assert _dump_refs(a) == _dump_refs(t)
    a_dump = _dump_refs(a)
    fabricated = [r for r in a_dump if r["ref"]["owner"] == "FabricatedThing"]
    assert len(fabricated) >= 1


def test_references_raise_statement(_reset_engine):
    src = (
        "def f():\n"
        "    raise CustomError\n"
    )
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(
        extract_references, src, "x.py", lookup, None, None,
        _reset_engine=_reset_engine,
    )
    assert _dump_refs(a) == _dump_refs(t)
    a_dump = _dump_refs(a)
    assert any(r["context"] == "raise CustomError" for r in a_dump)


def test_references_skip_typevar_local(_reset_engine):
    # T is a TypeVar → should NOT appear as a class reference
    src = (
        "from typing import TypeVar\n"
        "T = TypeVar('T')\n"
        "def f(x: T) -> T:\n"
        "    return x\n"
    )
    lookup = StructuralIndexLookup(index_text="")
    a, t = _run_both(
        extract_references, src, "x.py", lookup, None, None,
        _reset_engine=_reset_engine,
    )
    assert _dump_refs(a) == _dump_refs(t)
    a_dump = _dump_refs(a)
    assert not any(r["ref"]["owner"] == "T" for r in a_dump if r["ref"]["kind"] == "class")


def test_references_async_iter_propagation(_reset_engine, tmp_path):
    # Set up a codebase where service.stream() returns AsyncIterator[str]
    (tmp_path / "svc.py").write_text(
        "from typing import AsyncIterator\n"
        "class Service:\n"
        "    async def stream(self, q: str) -> AsyncIterator[str]:\n"
        "        yield q\n"
    )

    src = (
        "def handler(service: Service) -> None:\n"
        "    result = service.stream('q')\n"
        "    async for x in result:\n"
        "        print(x)\n"
    )

    def build():
        lookup = StructuralIndexLookup(index_text="")
        lookup.augment_from_source_dir(str(tmp_path))
        return lookup

    _index_mod.set_engine("ast")
    a = extract_references(src, "handler.py", build(), None, None)
    _index_mod.set_engine("tree_sitter")
    t = extract_references(src, "handler.py", build(), None, None)
    assert _dump_refs(a) == _dump_refs(t)
