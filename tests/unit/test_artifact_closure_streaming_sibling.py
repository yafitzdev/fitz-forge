# tests/unit/test_artifact_closure_streaming_sibling.py
"""Tests for the streaming-sibling closure invariant.

The invariant: when an artifact method claims to stream (has ``yield``
or returns Iterator/Generator/AsyncIterator/AsyncGenerator) and calls
``obj.foo(...)`` on a typed local whose class has BOTH ``foo`` and a
streaming sibling ``stream_foo``/``foo_stream``/iterator-typed variant
(detected in disk source OR sibling artifacts), the artifact should
have called the streaming sibling. Set-level — per-artifact validation
cannot see it.
"""

from __future__ import annotations

import textwrap

from fitz_forge.planning.artifact.closure import check_closure
from fitz_forge.planning.validation.grounding import StructuralIndexLookup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lookup(structural_index_text: str = "") -> StructuralIndexLookup:
    return StructuralIndexLookup(structural_index_text)


def _streaming_violations(violations):
    return [v for v in violations if v.kind == "streaming-sibling"]


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------


def test_self_attr_typed_local_calls_blocking_when_stream_exists():
    """The canonical B9 shape: engine.stream_query yields and calls
    self._synth.generate(...) — but Synth has both generate AND
    stream_generate, so the streaming variant should have been called.
    """
    engine_src = textwrap.dedent(
        """
        from typing import Iterator
        class Engine:
            def __init__(self, synth: Synth) -> None:
                self._synth: Synth = synth
            def stream_query(self, q: str) -> Iterator[str]:
                answer = self._synth.generate(q)
                yield answer
        """
    ).lstrip()
    synth_src = textwrap.dedent(
        """
        from typing import Iterator
        class Synth:
            def generate(self, q: str) -> str:
                return ""
            def stream_generate(self, q: str) -> Iterator[str]:
                yield ""
        """
    ).lstrip()

    artifacts = [
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
        {"filename": "synth.py", "content": synth_src, "purpose": "synth"},
    ]
    lookup = _make_lookup()
    violations = _streaming_violations(check_closure(artifacts, lookup))
    assert len(violations) == 1
    v = violations[0]
    assert v.artifact == "engine.py"
    assert v.ref.owner == "Synth"
    assert v.ref.name == "generate"
    assert "stream_generate" in v.detail
    assert "stream_query" in v.detail
    assert "delegate to streaming siblings" in v.detail


def test_iterator_return_type_triggers_check_without_yield():
    """A method whose return type is Iterator[...] should be treated as
    streaming even without an explicit yield body (e.g. delegating to
    another generator)."""
    engine_src = textwrap.dedent(
        """
        from typing import Iterator
        class Engine:
            def __init__(self, synth: Synth) -> None:
                self._synth: Synth = synth
            def stream_query(self, q: str) -> Iterator[str]:
                return self._synth.generate(q)
        """
    ).lstrip()
    synth_src = textwrap.dedent(
        """
        from typing import Iterator
        class Synth:
            def generate(self, q: str) -> str:
                return ""
            def stream_generate(self, q: str) -> Iterator[str]:
                yield ""
        """
    ).lstrip()
    artifacts = [
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
        {"filename": "synth.py", "content": synth_src, "purpose": "synth"},
    ]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert len(violations) == 1
    assert violations[0].ref.name == "generate"


def test_typed_local_call_triggers_violation():
    """Variable bound by `var = C(...)` inside a streaming method should
    be tracked and produce a violation when the call is on the blocking
    method."""
    src = textwrap.dedent(
        """
        from typing import Iterator
        class Caller:
            def stream(self) -> Iterator[str]:
                obj = Helper()
                obj.foo()
                yield ""
        class Helper:
            def foo(self) -> str:
                return ""
            def stream_foo(self) -> Iterator[str]:
                yield ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert len(violations) == 1
    assert violations[0].ref.owner == "Helper"
    assert violations[0].ref.name == "foo"


def test_streaming_sibling_in_disk_source_only(tmp_path):
    """Sibling lives only in disk source — the artifact streams and
    calls the blocking method. The disk-augmented lookup must surface
    the streaming sibling."""
    target_pkg = tmp_path / "pkg"
    target_pkg.mkdir()
    (target_pkg / "__init__.py").write_text("")
    (target_pkg / "synth.py").write_text(
        textwrap.dedent(
            """
            from typing import Iterator
            class Synth:
                def generate(self, q: str) -> str:
                    return ""
                def stream_generate(self, q: str) -> Iterator[str]:
                    yield ""
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (target_pkg / "engine.py").write_text(
        textwrap.dedent(
            """
            from .synth import Synth
            class Engine:
                def __init__(self, synth: Synth) -> None:
                    self._synth: Synth = synth
            """
        ).lstrip(),
        encoding="utf-8",
    )

    engine_artifact_src = textwrap.dedent(
        """
        from typing import Iterator
        from .synth import Synth
        class Engine:
            def __init__(self, synth: Synth) -> None:
                self._synth: Synth = synth
            def stream_query(self, q: str) -> Iterator[str]:
                answer = self._synth.generate(q)
                yield answer
        """
    ).lstrip()

    lookup = _make_lookup()
    lookup.augment_from_source_dir(str(tmp_path))

    artifacts = [
        {
            "filename": "pkg/engine.py",
            "content": engine_artifact_src,
            "purpose": "engine",
        }
    ]
    violations = _streaming_violations(
        check_closure(artifacts, lookup, source_dir=str(tmp_path))
    )
    assert len(violations) == 1
    assert violations[0].ref.name == "generate"
    assert "stream_generate" in violations[0].detail


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_no_streaming_sibling_no_violation():
    """If the target class has no streaming variant, no violation."""
    src = textwrap.dedent(
        """
        from typing import Iterator
        class Caller:
            def stream(self) -> Iterator[str]:
                obj = Helper()
                obj.foo()
                yield ""
        class Helper:
            def foo(self) -> str:
                return ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert violations == []


def test_already_calls_streaming_sibling_no_violation():
    """Streaming method that already calls the streaming variant — fine."""
    src = textwrap.dedent(
        """
        from typing import Iterator
        class Caller:
            def stream(self) -> Iterator[str]:
                obj = Helper()
                yield from obj.stream_foo()
        class Helper:
            def foo(self) -> str:
                return ""
            def stream_foo(self) -> Iterator[str]:
                yield ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert violations == []


def test_call_inside_nested_function_no_violation():
    """Calls inside a nested def inside the streaming method are not
    counted (matches _iter_body_skipping_nested semantics)."""
    src = textwrap.dedent(
        """
        from typing import Iterator
        class Caller:
            def stream(self) -> Iterator[str]:
                def inner():
                    obj = Helper()
                    return obj.foo()  # inside nested def
                yield inner()
        class Helper:
            def foo(self) -> str:
                return ""
            def stream_foo(self) -> Iterator[str]:
                yield ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert violations == []


def test_untyped_local_no_violation():
    """If we can't resolve `obj`'s type, we skip rather than emit."""
    src = textwrap.dedent(
        """
        from typing import Iterator
        class Caller:
            def stream(self) -> Iterator[str]:
                obj = mystery_factory()
                obj.foo()
                yield ""
        class Helper:
            def foo(self) -> str:
                return ""
            def stream_foo(self) -> Iterator[str]:
                yield ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert violations == []


def test_non_streaming_method_no_violation():
    """A non-streaming method calling the blocking variant is fine — the
    invariant only fires for methods that claim to stream."""
    src = textwrap.dedent(
        """
        class Caller:
            def query(self, q: str) -> str:
                obj = Helper()
                return obj.foo()
        class Helper:
            def foo(self) -> str:
                return ""
            def stream_foo(self):
                yield ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert violations == []


def test_already_streaming_named_method_skipped():
    """If the call is already to a stream_-prefixed or _stream-suffixed
    method, no violation regardless of any other variants on the class."""
    src = textwrap.dedent(
        """
        from typing import Iterator
        class Caller:
            def stream(self) -> Iterator[str]:
                obj = Helper()
                yield from obj.stream_foo()
        class Helper:
            def foo(self) -> str:
                return ""
            def stream_foo(self) -> Iterator[str]:
                yield ""
        """
    ).lstrip()
    artifacts = [{"filename": "mod.py", "content": src, "purpose": "mod"}]
    violations = _streaming_violations(check_closure(artifacts, _make_lookup()))
    assert violations == []


# ---------------------------------------------------------------------------
# Cross-language safety: a TS file should not crash the closure check
# ---------------------------------------------------------------------------


def test_typescript_artifact_does_not_crash():
    """Closure check is Python-AST based — a TypeScript file should be
    skipped (parse_python returns None) without crashing."""
    ts_src = textwrap.dedent(
        """
        export class Engine {
            constructor(private synth: Synth) {}
            async *streamQuery(q: string): AsyncIterable<string> {
                const answer = await this.synth.generate(q);
                yield answer;
            }
        }
        """
    ).lstrip()
    artifacts = [{"filename": "engine.ts", "content": ts_src, "purpose": "engine"}]
    # Should not raise
    violations = check_closure(artifacts, _make_lookup())
    # No streaming-sibling violations from a non-Python file
    assert _streaming_violations(violations) == []


# ---------------------------------------------------------------------------
# Wiring: the new violation kind flows through the regenerate path
# ---------------------------------------------------------------------------


def test_violation_kind_routes_to_regenerate_not_expand():
    """generator.py routes streaming-sibling into to_regenerate, not
    to_expand. Smoke-check by verifying the kind is in the regenerate
    branch's accepted list."""
    import inspect

    from fitz_forge.planning.artifact import generator

    src = inspect.getsource(generator.generate_artifact_set)
    assert "streaming-sibling" in src
    # And it's grouped with the regenerate kinds.
    assert (
        "\"usage\", \"kwargs\", \"field\", \"import\", \"streaming-sibling\"" in src
        or "'usage', 'kwargs', 'field', 'import', 'streaming-sibling'" in src
    )
