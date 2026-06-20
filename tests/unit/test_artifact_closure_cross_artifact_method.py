# tests/unit/test_artifact_closure_cross_artifact_method.py
"""Tests for the cross-artifact method-existence invariant (B10).

Invariant: for every call ``instance.method(...)`` where ``instance``
resolves to a class C (via constructor param annotation, ``var =
ClassName(...)``, service-locator return type, or ``self._attr``
typing), ``method`` must exist on C. C's method set is the union of
sibling-artifact methods and disk methods. If ``method`` is missing,
emit a violation that names the actual methods on C as suggestions.

Set-level: no individual artifact can detect this — the offender's
file parses fine, the callee's file parses fine. Only the union sees
the drift.
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


def _missing_violations(violations):
    return [v for v in violations if v.kind == "missing"]


# ---------------------------------------------------------------------------
# Positive cases — drift is detected
# ---------------------------------------------------------------------------


def test_self_attr_typed_local_calls_fabricated_method():
    """The canonical B10 shape: service.query yields from
    self._engine.stream_answer(...) but the engine artifact only defines
    stream_query. The drift must be flagged with stream_query suggested.
    """
    service_src = textwrap.dedent(
        """
        from typing import Iterator
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def query(self, q: str) -> Iterator[str]:
                yield from self._engine.stream_answer(q)
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        from typing import Iterator
        class Engine:
            def stream_query(self, q: str) -> Iterator[str]:
                yield ""
        """
    ).lstrip()

    artifacts = [
        {"filename": "service.py", "content": service_src, "purpose": "service"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    assert len(viols) == 1, [v.pretty() for v in viols]
    v = viols[0]
    assert v.artifact == "service.py"
    assert v.ref.owner == "Engine"
    assert v.ref.name == "stream_answer"
    assert "stream_query" in v.detail
    assert "Engine.stream_answer" in v.detail


def test_constructor_param_typed_local_calls_fabricated_method():
    """Param annotation is the typing source: route(service: Service)
    calls service.fabricated_method() — Service has only real_method.
    """
    route_src = textwrap.dedent(
        """
        def handle(service: Service):
            return service.fabricated_method()
        """
    ).lstrip()
    service_src = textwrap.dedent(
        """
        class Service:
            def real_method(self):
                return 0
        """
    ).lstrip()

    artifacts = [
        {"filename": "route.py", "content": route_src, "purpose": "route"},
        {"filename": "service.py", "content": service_src, "purpose": "service"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    matches = [v for v in viols if v.ref.name == "fabricated_method"]
    assert len(matches) == 1
    v = matches[0]
    assert v.ref.owner == "Service"
    assert "real_method" in v.detail


def test_var_classname_constructor_calls_fabricated_method():
    """Local typed via `var = ClassName()`: factory builds an Engine and
    calls a method that doesn't exist on Engine.
    """
    factory_src = textwrap.dedent(
        """
        def build():
            engine = Engine()
            return engine.fabricated()
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        class Engine:
            def real_method(self):
                return 0
        """
    ).lstrip()

    artifacts = [
        {"filename": "factory.py", "content": factory_src, "purpose": "factory"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    matches = [v for v in viols if v.ref.name == "fabricated"]
    assert len(matches) == 1
    assert matches[0].ref.owner == "Engine"
    assert "real_method" in matches[0].detail


# ---------------------------------------------------------------------------
# Negative cases — must NOT fire
# ---------------------------------------------------------------------------


def test_real_method_on_sibling_artifact_does_not_fire():
    """The method call resolves to a real method defined on the sibling
    artifact's class — no violation.
    """
    service_src = textwrap.dedent(
        """
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def query(self):
                return self._engine.real_existing_method()
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        class Engine:
            def real_existing_method(self):
                return 0
        """
    ).lstrip()

    artifacts = [
        {"filename": "service.py", "content": service_src, "purpose": "service"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    matches = [v for v in viols if v.ref.owner == "Engine"]
    assert matches == [], [v.pretty() for v in matches]


def test_untyped_local_is_skipped():
    """When the call target's type can't be resolved (untyped local
    parameter), the check is out of scope. Per existing handling — no
    violation fires.
    """
    src = textwrap.dedent(
        """
        def handle(something):
            return something.method()
        """
    ).lstrip()

    artifacts = [{"filename": "x.py", "content": src, "purpose": "x"}]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    assert viols == [], [v.pretty() for v in viols]


def test_method_exists_on_disk_source_does_not_fire():
    """When the method is provided by the codebase's structural index
    (disk source), the call is satisfied — no violation.
    """
    structural_index = textwrap.dedent(
        """
        ## lib/engine.py
        classes: Engine[method_from_disk -> str]
        """
    ).strip()
    service_src = textwrap.dedent(
        """
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def query(self):
                return self._engine.method_from_disk()
        """
    ).lstrip()

    artifacts = [{"filename": "service.py", "content": service_src, "purpose": "s"}]
    viols = _missing_violations(check_closure(artifacts, _make_lookup(structural_index)))
    matches = [v for v in viols if v.ref.owner == "Engine"]
    assert matches == [], [v.pretty() for v in matches]


def test_method_exists_on_sibling_artifact_does_not_fire():
    """When a sibling artifact (engine.py) defines the new method, the
    service call to it must NOT fire a violation — the closure is
    satisfied across the artifact set.
    """
    service_src = textwrap.dedent(
        """
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def query(self):
                return self._engine.new_method()
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        class Engine:
            def new_method(self):
                return 0
        """
    ).lstrip()

    artifacts = [
        {"filename": "service.py", "content": service_src, "purpose": "service"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    matches = [v for v in viols if v.ref.owner == "Engine"]
    assert matches == [], [v.pretty() for v in matches]


# ---------------------------------------------------------------------------
# Integration — multi-link chain mirroring B10's real-world shape
# ---------------------------------------------------------------------------


def test_chain_route_service_engine_synth_aligned_no_violations():
    """When every cross-artifact method name aligns, no violations fire.
    Three-link chain: route -> service -> engine -> synth.
    """
    route_src = textwrap.dedent(
        """
        def handle(service: Service):
            return service.handle_request()
        """
    ).lstrip()
    service_src = textwrap.dedent(
        """
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def handle_request(self):
                return self._engine.run_query()
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        class Engine:
            def __init__(self, synth: Synth) -> None:
                self._synth: Synth = synth
            def run_query(self):
                return self._synth.generate()
        """
    ).lstrip()
    synth_src = textwrap.dedent(
        """
        class Synth:
            def generate(self):
                return ""
        """
    ).lstrip()

    artifacts = [
        {"filename": "route.py", "content": route_src, "purpose": "route"},
        {"filename": "service.py", "content": service_src, "purpose": "service"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
        {"filename": "synth.py", "content": synth_src, "purpose": "synth"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    cross_artifact = [
        v for v in viols if v.ref.owner in ("Service", "Engine", "Synth") and v.ref.name is not None
    ]
    assert cross_artifact == [], [v.pretty() for v in cross_artifact]


def test_chain_mid_link_drift_fires_one_violation_per_offender():
    """Drift in the middle of the chain: engine calls
    self._synth.fabricated() but Synth provides only generate.
    The violation must fire on engine.py with Synth's actual methods
    suggested. Other links are clean and must not fire.
    """
    route_src = textwrap.dedent(
        """
        def handle(service: Service):
            return service.handle_request()
        """
    ).lstrip()
    service_src = textwrap.dedent(
        """
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def handle_request(self):
                return self._engine.run_query()
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        class Engine:
            def __init__(self, synth: Synth) -> None:
                self._synth: Synth = synth
            def run_query(self):
                return self._synth.fabricated()
        """
    ).lstrip()
    synth_src = textwrap.dedent(
        """
        class Synth:
            def generate(self):
                return ""
        """
    ).lstrip()

    artifacts = [
        {"filename": "route.py", "content": route_src, "purpose": "route"},
        {"filename": "service.py", "content": service_src, "purpose": "service"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
        {"filename": "synth.py", "content": synth_src, "purpose": "synth"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    drift = [v for v in viols if v.ref.owner == "Synth" and v.ref.name == "fabricated"]
    assert len(drift) == 1
    assert drift[0].artifact == "engine.py"
    assert "generate" in drift[0].detail
    # And no spurious violations on the aligned links
    spurious_engine = [v for v in viols if v.ref.owner == "Engine" and v.ref.name == "run_query"]
    assert spurious_engine == []
    spurious_service = [
        v for v in viols if v.ref.owner == "Service" and v.ref.name == "handle_request"
    ]
    assert spurious_service == []


def test_close_match_suggestion_orders_by_similarity():
    """When several candidates exist, the closest spelling wins —
    `stream_answer` is closer to `stream_query` than to `unrelated`.
    """
    service_src = textwrap.dedent(
        """
        from typing import Iterator
        class Service:
            def __init__(self, engine: Engine) -> None:
                self._engine: Engine = engine
            def query(self, q: str) -> Iterator[str]:
                yield from self._engine.stream_answer(q)
        """
    ).lstrip()
    engine_src = textwrap.dedent(
        """
        from typing import Iterator
        class Engine:
            def stream_query(self, q: str) -> Iterator[str]:
                yield ""
            def unrelated(self):
                return 0
            def configure(self):
                return None
        """
    ).lstrip()

    artifacts = [
        {"filename": "service.py", "content": service_src, "purpose": "service"},
        {"filename": "engine.py", "content": engine_src, "purpose": "engine"},
    ]
    viols = _missing_violations(check_closure(artifacts, _make_lookup()))
    drift = [v for v in viols if v.ref.name == "stream_answer"]
    assert len(drift) == 1
    detail = drift[0].detail
    # Closest match (shared 'stream_' prefix) appears before others.
    assert detail.index("stream_query") < detail.index("unrelated")
