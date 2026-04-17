# tests/unit/test_ts_engine_switch.py
"""Verify ``StructuralIndexLookup.augment_from_source_dir`` is identical
under ``ast`` and ``tree_sitter`` engines.

This is the integration gate for the migration: the public method that
every caller touches must produce the same index no matter which parser
runs underneath. If this test ever fails, do not flip the default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fitz_forge.planning.validation.grounding import index as _index_mod
from fitz_forge.planning.validation.grounding.index import StructuralIndexLookup
from tests.unit.test_ts_index_parity import FIXTURE_FILES, _snapshot, _write_tree


@pytest.fixture
def _reset_engine():
    """Ensure each test restores the global engine after running."""
    original = _index_mod.get_engine()
    yield
    _index_mod.set_engine(original)


def test_both_engines_produce_identical_index(
    tmp_path: Path, _reset_engine
) -> None:
    _write_tree(tmp_path, FIXTURE_FILES)

    _index_mod.set_engine("ast")
    ast_lookup = StructuralIndexLookup(index_text="")
    ast_lookup.augment_from_source_dir(str(tmp_path))

    _index_mod.set_engine("tree_sitter")
    ts_lookup = StructuralIndexLookup(index_text="")
    ts_lookup.augment_from_source_dir(str(tmp_path))

    assert _snapshot(ast_lookup) == _snapshot(ts_lookup)


def test_set_engine_rejects_unknown(_reset_engine) -> None:
    with pytest.raises(ValueError, match="Unknown engine"):
        _index_mod.set_engine("wat")


def test_default_engine_is_tree_sitter() -> None:
    # Default is now tree-sitter after parity was proven on fitz-forge
    # itself. Flip back to "ast" if a regression appears — the routing
    # stays in place.
    assert _index_mod.get_engine() == "tree_sitter"
