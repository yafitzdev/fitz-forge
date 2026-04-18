# tests/unit/test_artifact_validate_unbound_names.py
"""Tests for the per-artifact unbound-name scope-resolution check (B11).

Invariant: every name read inside a function body must resolve to a
parameter, a local binding, a module-level import, a sibling top-level
def/class in the same artifact, or a Python builtin (`self`/`cls` are
implicit method receivers).

Per-artifact and per-method — no closure / sibling-artifact machinery.
Catches the route shape where the body uses ``request.X`` but the
signature is flat positional params (NameError at runtime, invisible to
per-artifact AST checks because the file parses fine).
"""

from __future__ import annotations

import textwrap

from fitz_forge.planning.artifact.context import ArtifactContext
from fitz_forge.planning.artifact.validate import (
    _check_unbound_names,
    validate,
)


def _ctx(filename: str = "any.py", purpose: str = "") -> ArtifactContext:
    return ArtifactContext(filename=filename, purpose=purpose)


def _unbound(errors):
    return [e for e in errors if e.check == "unbound_name"]


def _names(errors) -> set[str]:
    """Extract the offending name from each unbound_name message."""
    out: set[str] = set()
    for e in errors:
        # "unbound_name: 'request' referenced at line N..."
        msg = e.message
        if "'" in msg:
            try:
                out.add(msg.split("'")[1])
            except IndexError:
                pass
    return out


# ---------------------------------------------------------------------------
# Positive — the bug: route signature has flat params, body uses `request`
# ---------------------------------------------------------------------------


def test_b11_route_flat_params_body_uses_request():
    """Canonical B11 shape: signature accepts question/source/etc as
    positional params but body references request.X — `request` must be
    flagged with the param names suggested.
    """
    src = textwrap.dedent(
        """
        async def stream_query(question: str, source: str = None,
                               collection: str = "default", top_k: int = 5,
                               conversation_history: list = None):
            src = request.source
            q = request.question
            yield request.collection
            return request.top_k
        """
    ).lstrip()
    errors = _check_unbound_names(src, _ctx("routes/stream.py"))
    unbound = _unbound(errors)
    assert "request" in _names(unbound), [e.message for e in unbound]
    # Suggestion must include at least one real param name.
    msg = next(e for e in unbound if "'request'" in e.message)
    assert any(p in msg.suggestion for p in ("question", "source", "collection")), msg.suggestion


def test_b11_typo_param_name_flagged_with_suggestion():
    """Function uses `data` but the param is `daata` — the typo's load
    is flagged with `daata` as the closest match."""
    src = textwrap.dedent(
        """
        def f(daata):
            return data
        """
    ).lstrip()
    errors = _check_unbound_names(src, _ctx())
    unbound = _unbound(errors)
    assert "data" in _names(unbound)
    msg = next(e for e in unbound if "'data'" in e.message)
    assert "daata" in msg.suggestion


def test_b11_emits_via_validate_pipeline():
    """The check is wired into validate() — not just callable directly."""
    src = textwrap.dedent(
        """
        def f(x):
            return ghost
        """
    ).lstrip()
    errors = validate(src, _ctx())
    assert any(e.check == "unbound_name" and "'ghost'" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Negative — bound names must not be flagged
# ---------------------------------------------------------------------------


def test_param_use_not_flagged():
    src = textwrap.dedent(
        """
        def f(a, b: int, c=1, *args, **kwargs):
            return a + b + c + args[0] + kwargs.get('x')
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_self_and_cls_not_flagged():
    """Method bodies can use `self.x` / `cls.y` without flagging `self`/`cls`."""
    src = textwrap.dedent(
        """
        class C:
            def method(self):
                return self.value + self._helper()
            @classmethod
            def factory(cls):
                return cls.default
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_import_not_flagged():
    """`import os` makes `os.path.join(...)` fine — `os` resolves."""
    src = textwrap.dedent(
        """
        import os
        from typing import List, Dict as D
        import asyncio as aio

        def f(items: List[int]):
            paths = os.path.join('a', 'b')
            store: D[str, int] = {}
            return aio.run(store), paths
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_builtin_not_flagged():
    src = textwrap.dedent(
        """
        def f(items):
            for i, x in enumerate(items):
                print(len(x))
            return list(items) + [True, False, None]
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_for_with_except_bindings_not_flagged():
    src = textwrap.dedent(
        """
        def f(seq, p):
            for x in seq:
                pass
            with open(p) as g:
                g.read()
            try:
                pass
            except Exception as e:
                str(e)
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_sibling_top_level_def_not_flagged():
    """Function calls another sibling top-level function in same artifact."""
    src = textwrap.dedent(
        """
        def helper(x):
            return x + 1

        def main(items):
            return [helper(i) for i in items]
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_sibling_top_level_class_not_flagged():
    """Function instantiates a sibling top-level class in same artifact."""
    src = textwrap.dedent(
        """
        class Box:
            def __init__(self, v): self.v = v

        def make(v):
            return Box(v)
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_assignment_target_not_flagged_even_when_unknown():
    """`x = some_call()` — `x` is a binding, not a reference, so the
    unknown name `x` doesn't trigger; only `some_call` would (and it's
    a builtin-shaped call, but `some_call` isn't a builtin so it WILL
    be flagged — that's correct, this test asserts only `x` is silent).
    """
    src = textwrap.dedent(
        """
        def f():
            x = 5
            return x
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_nested_function_param_not_flagged():
    """Nested def's params bind locally; outer scope doesn't see `z`,
    but the inner's body uses `z` — must not flag `z` because the inner
    function is walked separately with its own param set."""
    src = textwrap.dedent(
        """
        def outer():
            def inner(z):
                return z + 1
            return inner(5)
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_comprehension_target_not_flagged():
    src = textwrap.dedent(
        """
        def f(items, pairs):
            a = [i for i in items]
            b = {k: v for k, v in pairs}
            c = {x for x in items}
            d = (y for y in items)
            return a, b, c, d
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_walrus_target_not_flagged():
    src = textwrap.dedent(
        """
        def f(items):
            if (n := len(items)) > 0:
                return n
            return 0
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_keyword_argument_key_not_flagged():
    """`foo(name=value)` — `name` is the param key on `foo`, not a load
    in this scope. Only `value` should be checked as a load."""
    src = textwrap.dedent(
        """
        def f(value):
            return dict(name=value, other=1)
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_global_nonlocal_not_flagged():
    src = textwrap.dedent(
        """
        counter = 0
        def f():
            global counter
            counter += 1
            return counter
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_module_level_assignment_visible_in_function():
    src = textwrap.dedent(
        """
        DEFAULT = "x"
        def f():
            return DEFAULT
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_star_import_suppresses_check():
    """`from M import *` brings unknown names; we suppress the check
    for the whole artifact rather than emit false positives."""
    src = textwrap.dedent(
        """
        from random_module import *
        def f():
            return whatever_is_starred_in
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_unparseable_artifact_silent():
    """Parse-fail artifacts are flagged by _check_parseable; the
    unbound check must not crash or emit on them."""
    src = "def broken(:\n    return"
    # Don't go through `validate()` — the parseable check would block;
    # call the unbound check directly to confirm it returns [] silently.
    assert _check_unbound_names(src, _ctx()) == []


def test_attribute_chain_only_flags_leftmost():
    """`a.b.c` — only `a` is checked. The `.b` and `.c` parts are field
    access, not name references."""
    src = textwrap.dedent(
        """
        import os
        def f():
            return os.path.join.somemethod
        """
    ).lstrip()
    # `os` is imported. `path`, `join`, `somemethod` are attribute
    # accesses and must NOT be flagged.
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_default_param_value_referencing_name():
    """`def f(x=DEFAULT): ...` — `DEFAULT` is a load and should be
    flagged if not bound."""
    src = textwrap.dedent(
        """
        def f(x=DEFAULT):
            return x
        """
    ).lstrip()
    errors = _unbound(_check_unbound_names(src, _ctx()))
    assert "DEFAULT" in _names(errors)


def test_default_param_value_with_constant_ok():
    src = textwrap.dedent(
        """
        def f(x=5, y="hi"):
            return x, y
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_one_violation_per_name_per_method():
    """Same name referenced multiple times in one method emits one error."""
    src = textwrap.dedent(
        """
        def f():
            a = ghost.x
            b = ghost.y
            c = ghost.z
            return a, b, c
        """
    ).lstrip()
    errors = _unbound(_check_unbound_names(src, _ctx()))
    assert len(errors) == 1
    assert "ghost" in _names(errors)


def test_skips_non_python_files():
    """TypeScript / Go / Rust files don't run AST checks at all."""
    src = "function foo() { return ghost; }"
    errors = validate(src, _ctx("foo.ts"))
    assert not [e for e in errors if e.check == "unbound_name"]


def test_method_inside_class_uses_init_attribute_via_self():
    """`self.x = ...` in __init__ then `self.x` in another method — no
    flag, because `self` is bound and field access on `self` is out of
    scope for this check."""
    src = textwrap.dedent(
        """
        class C:
            def __init__(self):
                self.engine = None
            def use(self):
                return self.engine.stream()
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_lambda_params_bind_locally():
    src = textwrap.dedent(
        """
        def f():
            g = lambda a, b: a + b
            return g(1, 2)
        """
    ).lstrip()
    # `a`/`b` inside lambda are params of the lambda, not the outer
    # function. The outer function walks and sees `g` (local binding)
    # plus the lambda call args (literals). The lambda body itself is
    # NOT walked by the outer-function pass (skipped at lambda boundary).
    # We don't currently walk lambdas as separate scopes (they're
    # expression-level), so this test asserts no false positives.
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_with_multiple_as_targets_bound():
    src = textwrap.dedent(
        """
        def f(p, q):
            with open(p) as a, open(q) as b:
                return a.read() + b.read()
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []


def test_tuple_unpack_in_for_bound():
    src = textwrap.dedent(
        """
        def f(pairs):
            for k, v in pairs:
                print(k, v)
        """
    ).lstrip()
    assert _unbound(_check_unbound_names(src, _ctx())) == []
