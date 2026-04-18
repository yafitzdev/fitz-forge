# tests/unit/test_extract_provides_surgical.py
"""Tests for B15: `extract_provides` must use the caller-provided strategy
classification (surgical vs new_code) as the canonical source of truth.

The artifact pipeline already knows which strategy generated each artifact
(`SurgicalRewriteStrategy` vs `NewCodeStrategy`). Closure analysis must
respect that classification — re-deriving it from a content-shape heuristic
misclassifies dedented surgical artifacts (def at column 0) as top-level
functions, which silently breaks every downstream invariant that depends
on cross-artifact class-method awareness (B9 streaming-sibling, existence,
kwargs, field).
"""

from __future__ import annotations

import textwrap

from fitz_forge.planning.artifact.closure import SymbolRef, extract_provides
from fitz_forge.planning.validation.grounding import StructuralIndexLookup


def _lookup_with_synth(file: str = "synthesizer.py") -> StructuralIndexLookup:
    """Lookup containing a single Synth class on the given file path."""
    index_text = textwrap.dedent(
        f"""
        ## {file}
        classes: Synth[generate -> str]
        """
    ).strip()
    return StructuralIndexLookup(index_text)


# ---------------------------------------------------------------------------
# Positive: the failing real-world case
# ---------------------------------------------------------------------------


def test_dedented_surgical_explicit_is_surgical_registers_as_method():
    """Dedented surgical content (def at column 0) with explicit
    `is_surgical=True` registers the function as a method on the target
    class. This is the canonical B15 fix path."""
    content = textwrap.dedent(
        """
        def stream_query(
            self,
            query: str,
        ):
            yield query
        """
    ).lstrip()
    lookup = _lookup_with_synth("synthesizer.py")
    out = extract_provides(content, "synthesizer.py", lookup, is_surgical=True)
    assert SymbolRef(owner="Synth", name="stream_query", kind="method") in out
    # Should NOT also be registered as a top-level function under that name.
    assert SymbolRef(owner=None, name="stream_query", kind="function") not in out


def test_dedented_surgical_default_falls_back_to_heuristic():
    """Without explicit is_surgical, the legacy heuristic misclassifies
    dedented content as new code — registering as a top-level function.
    This proves the heuristic is what was breaking B9; the explicit
    parameter is the fix."""
    content = textwrap.dedent(
        """
        def stream_query(self, query: str):
            yield query
        """
    ).lstrip()
    lookup = _lookup_with_synth("synthesizer.py")
    out = extract_provides(content, "synthesizer.py", lookup)  # no is_surgical
    assert SymbolRef(owner=None, name="stream_query", kind="function") in out
    assert SymbolRef(owner="Synth", name="stream_query", kind="method") not in out


# ---------------------------------------------------------------------------
# Backward compatibility: existing heuristic still works for indented input
# ---------------------------------------------------------------------------


def test_indented_surgical_default_heuristic_still_works():
    """Legacy callers (no is_surgical arg) still get the right answer
    when the surgical content is indented — the original heuristic
    pathway. Ensures backward compatibility with test code or any
    one-off caller that doesn't have strategy info."""
    content = "    def foo(self):\n        return 1\n"
    lookup = _lookup_with_synth("synthesizer.py")
    out = extract_provides(content, "synthesizer.py", lookup)
    assert SymbolRef(owner="Synth", name="foo", kind="method") in out


# ---------------------------------------------------------------------------
# Positive: explicit new_code with full class
# ---------------------------------------------------------------------------


def test_new_code_full_class_registers_methods_under_class():
    """Explicit is_surgical=False with a full class definition: methods
    register under the class as before. The explicit boolean does not
    disturb the new_code path."""
    content = textwrap.dedent(
        """
        class Synth:
            def generate(self, q: str) -> str:
                return ""
            def stream_query(self, q: str):
                yield ""
        """
    ).lstrip()
    lookup = _lookup_with_synth("synthesizer.py")
    out = extract_provides(content, "synthesizer.py", lookup, is_surgical=False)
    assert SymbolRef(owner="Synth", name="generate", kind="method") in out
    assert SymbolRef(owner="Synth", name="stream_query", kind="method") in out
    # Class itself is registered too.
    assert SymbolRef(owner="Synth", name=None, kind="class") in out


# ---------------------------------------------------------------------------
# Negative: surgical without a known target class
# ---------------------------------------------------------------------------


def test_surgical_without_target_class_falls_through_to_function():
    """is_surgical=True but the lookup doesn't know any class for this
    file — fall through to top-level function (don't crash, don't
    fabricate ownership)."""
    content = "def free_function():\n    return 1\n"
    # Empty lookup — no Synth, no anything for this file.
    lookup = StructuralIndexLookup("")
    out = extract_provides(content, "unknown.py", lookup, is_surgical=True)
    assert SymbolRef(owner=None, name="free_function", kind="function") in out
