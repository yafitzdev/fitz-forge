# tests/unit/test_ts_check_parity.py
"""Parity tests for the tree-sitter port of check.check_artifact.

The tree-sitter and ast paths must produce equivalent Violation lists
for the same artifact + structural index.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from fitz_forge.planning.validation.grounding import index as _index_mod
from fitz_forge.planning.validation.grounding.check import (
    Violation,
    _check_parallel_signatures,
    check_artifact,
)
from fitz_forge.planning.validation.grounding.index import StructuralIndexLookup


@pytest.fixture
def _reset_engine():
    original = _index_mod.get_engine()
    yield
    _index_mod.set_engine(original)


def _both_engines_agree(
    artifact: dict, lookup_builder, _reset_engine
) -> tuple[list[Violation], list[Violation]]:
    """Run check_artifact under both engines and return both results.

    The lookup_builder is a callable so each engine gets a fresh lookup
    (since augmentation differs between engines and could drift state).
    """
    _index_mod.set_engine("ast")
    ast_result = check_artifact(artifact, lookup_builder())
    _index_mod.set_engine("tree_sitter")
    ts_result = check_artifact(artifact, lookup_builder())
    return ast_result, ts_result


def _equal_violations(a: list[Violation], b: list[Violation]) -> bool:
    return [asdict(x) for x in a] == [asdict(x) for x in b]


def test_no_violations_on_clean_artifact(_reset_engine) -> None:
    # Artifact calls only stdlib / known classes
    content = (
        "class Thing:\n"
        "    def __init__(self):\n"
        "        self._items: list[int] = []\n"
        "\n"
        "    def method(self) -> int:\n"
        "        return len(self._items)\n"
    )
    artifact = {"filename": "app/thing.py", "content": content}
    build = lambda: StructuralIndexLookup(index_text="")
    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert ast_v == []
    assert ts_v == []


def test_parse_error_both_engines(_reset_engine) -> None:
    artifact = {"filename": "broken.py", "content": "def foo(:\n    return"}
    build = lambda: StructuralIndexLookup(index_text="")
    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert len(ast_v) == 1 and ast_v[0].kind == "parse_error"
    assert len(ts_v) == 1 and ts_v[0].kind == "parse_error"


def test_empty_content_both_engines(_reset_engine) -> None:
    artifact = {"filename": "x.py", "content": "   \n\n"}
    build = lambda: StructuralIndexLookup(index_text="")
    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert ast_v == []
    assert ts_v == []


def test_missing_method_on_self(_reset_engine) -> None:
    # self.foo() — foo doesn't exist anywhere
    content = "class X:\n    def bar(self):\n        self.foo()\n"
    artifact = {"filename": "x.py", "content": content}
    build = lambda: StructuralIndexLookup(index_text="")
    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert _equal_violations(ast_v, ts_v)
    # One missing_method each
    assert len(ast_v) == 1 and ast_v[0].kind == "missing_method"


def test_missing_method_skipped_when_known_elsewhere(_reset_engine) -> None:
    # self.foo() — foo exists on another class in the index
    content = "class X:\n    def bar(self):\n        self.foo()\n"
    artifact = {"filename": "x.py", "content": content}

    def build():
        # Index text declares another class with a `foo` method
        text = "## other.py\nclasses: Other [foo]\n"
        lookup = StructuralIndexLookup(index_text=text)
        return lookup

    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert ast_v == [] == ts_v


def test_missing_class_bare_call(_reset_engine) -> None:
    # TotallyMadeUp() — uppercase bare call, no such class
    content = "def f():\n    return TotallyMadeUp()\n"
    artifact = {"filename": "x.py", "content": content}
    build = lambda: StructuralIndexLookup(index_text="")
    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert _equal_violations(ast_v, ts_v)
    assert len(ast_v) == 1 and ast_v[0].kind == "missing_class"


def test_wrong_field_on_typed_local(_reset_engine) -> None:
    # obj: Foo; obj.missing — missing isn't a field/method of Foo
    content = (
        "def f(obj: Foo) -> None:\n"
        "    obj.missing\n"
    )
    artifact = {"filename": "x.py", "content": content}

    def build():
        text = "## pkg/foo.py\nclasses: Foo [real_method]\n"
        return StructuralIndexLookup(index_text=text)

    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert _equal_violations(ast_v, ts_v)
    assert len(ast_v) == 1 and ast_v[0].kind == "wrong_field"


def test_skip_names_not_flagged(_reset_engine) -> None:
    content = "def f():\n    return dict()\n"
    artifact = {"filename": "x.py", "content": content}
    build = lambda: StructuralIndexLookup(index_text="")
    ast_v, ts_v = _both_engines_agree(artifact, build, _reset_engine)
    assert ast_v == [] == ts_v


def test_parallel_signatures_parity(_reset_engine, tmp_path) -> None:
    # Use a real fixture tree so the index is populated via augment.
    orig_src = (
        "def generate(self, q, ctx, history, options, tools) -> Response:\n"
        "    return Response()\n"
    )
    (tmp_path / "svc_orig.py").write_text(orig_src)

    artifacts = [
        {
            "filename": "svc.py",
            "content": (
                "def generate_stream(self, q: str) -> None:\n"
                "    yield 1\n"
            ),
        }
    ]

    def build():
        lookup = StructuralIndexLookup(index_text="")
        lookup.augment_from_source_dir(str(tmp_path))
        return lookup

    _index_mod.set_engine("ast")
    ast_v = _check_parallel_signatures(artifacts, build())
    _index_mod.set_engine("tree_sitter")
    ts_v = _check_parallel_signatures(artifacts, build())
    assert _equal_violations(ast_v, ts_v)
    assert len(ast_v) == 1 and ast_v[0].kind == "param_mismatch"
