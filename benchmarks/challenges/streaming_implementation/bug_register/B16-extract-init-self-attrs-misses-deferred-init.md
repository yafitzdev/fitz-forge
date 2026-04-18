# B16 — `extract_init_self_attrs` misses self-attrs assigned in init helper methods

**Status:** resolved
**Impact:** 9/10 (blocked B9 even after B15)
**Opened:** 2026-04-18
**Closed:** 2026-04-18

**Fix:** `extract_init_self_attrs` now walks `__init__` AND
`_init_components` / `setup` / `_setup` (the same convention tuple
used by the newer `iter_init_self_assignments` helper). Later methods
override earlier ones so a final binding in `_init_components` wins
over a placeholder in `__init__`. Backward-compatible — classes
without init helpers behave identically.

**Validation:** Direct repro: `_synthesizer = CodeSynthesizer` now
resolves correctly (was missing). End-to-end via run_031 benchmark.

## Symptom

`extract_init_self_attrs` (`fitz_forge/planning/validation/grounding/
inference.py:552`) only walks the body of `__init__` for `self._x = ...`
assignments. Production classes commonly defer attribute initialisation
to a helper method called from `__init__`:

```python
class FitzKragEngine:
    def __init__(self, config: FitzKragConfig):
        self._config = config
        self._init_components()        # <-- defers _synthesizer setup

    def _init_components(self) -> None:
        ...
        self._synthesizer = CodeSynthesizer(self._chat, self._config)
```

`extract_init_self_attrs(FitzKragEngine)` returns `{_config, _bg_worker,
_manifest}` — missing `_synthesizer` because the assignment is in
`_init_components`, not `__init__`.

## Evidence

Replay of streaming_implementation B9 fix with B15 in place still produced
a broken plan (engine calls blocking `_synthesizer.generate()` and yields
the result). Direct repro on the produced artifacts:

```
engine self_attrs (from load_target_self_attrs):
  {'_manifest': 'Any', '_bg_worker': 'Any', '_config': 'FitzKragConfig'}
  _synthesizer typed as: NOT FOUND

streaming-sibling scanner violations on engine.py: 0
```

The scanner correctly resolves `CodeSynthesizer.stream_query` exists alongside
`CodeSynthesizer.generate` (B15 fix verified). But it cannot link the call
site `self._synthesizer.generate(...)` to `CodeSynthesizer` because
`_synthesizer` isn't in the typed-self-attrs map.

## Generalization

Invariant: **for any class C, the set of typed `self.*` attributes must be
the union of assignments across `__init__` and any helper methods
conventionally used for deferred init (`_init_components`, `setup`,
`_setup`, `__post_init__`, etc.).**

The codebase already has the right tuple defined (`inference.py:785`):

```python
_INIT_METHOD_NAMES: tuple[str, ...] = ("__init__", "_init_components", "setup", "_setup")
```

…and a newer helper (`iter_init_self_assignments`) that uses it. But
`extract_init_self_attrs` predates the helper and walks only `__init__`.

## Scope of the class

Affects every closure invariant that uses `self_attrs` for type tracking:

- B9 streaming-sibling check (this case)
- Per-artifact validation: any check that needs `self.<attr>` types
- Field access invariant
- Future invariants

The deferred-init pattern is common in DI-style codebases, plugin systems,
and anywhere `__init__` is light and most setup happens in helpers.

## Fix direction

Extend `extract_init_self_attrs` to walk all methods in `_INIT_METHOD_NAMES`
(not just `__init__`). Per-method, repeat the same three passes (class-level
annotations, param-typed assignments, ClassName(...) calls). Merge results
with later methods overriding earlier ones (so `_init_components` overrides
a placeholder set in `__init__`).

Single-line conceptual change. Backward-compatible: classes without init
helpers behave identically.

## Acceptance

- `extract_init_self_attrs(FitzKragEngine)` from disk returns a dict
  containing `_synthesizer: CodeSynthesizer`.
- The B9 streaming-sibling scanner fires on engine.py vs synthesizer.py
  artifacts in the replay scenario.
- No regression on existing extract_init_self_attrs tests.

## Relationship to B9 / B15

B9 needs B15 needs B16. The chain is:
1. B9 invariant correct, but inert without sibling class info.
2. B15 fix supplies sibling class info.
3. B16 fix supplies the `self._x` type that lets B9 resolve the call site
   onto that class.

After B16, B9's broader fix should fire on the dominant real-world variant
without further changes.
