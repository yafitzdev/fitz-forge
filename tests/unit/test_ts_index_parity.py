# tests/unit/test_ts_index_parity.py
"""Parity between ast-backed ``StructuralIndexLookup.augment_from_source_dir``
and the tree-sitter ``_ts_inference.augment_from_source_dir``.

Both take an empty lookup and a source tree; after augmentation, their
``classes``/``functions``/method-name/class-name/function-name sets must
match. We also compare every indexed method and field on a per-class
basis.
"""

from __future__ import annotations

from pathlib import Path

from fitz_forge.planning.validation.grounding import _ts_inference
from fitz_forge.planning.validation.grounding.index import StructuralIndexLookup


FIXTURE_FILES: dict[str, str] = {
    "app/routes.py": (
        "from typing import Iterator\n"
        "\n"
        "@api_route\n"                              # decorator on class
        "class Route:\n"
        "    path: str\n"
        "    method: str = 'GET'\n"
        "\n"
        "    @cached\n"                             # decorator on method
        "    def handle(self, req: Request) -> Response:\n"
        '        """Handle request.\n\n        Returns:\n            Response: the result.\n        """\n'
        "        return Response()\n"
        "\n"
        "    async def stream(self) -> Iterator[bytes]:\n"
        "        yield b''\n"
        "\n"
        "    class Nested:\n"                        # nested class
        "        kind: str\n"
        "\n"
        "def make_route(path: str, *args, **kwargs) -> Route:\n"
        "    return Route()\n"
        "\n"
        "async def async_top_level():\n"            # ast skips these
        "    return 1\n"
        "\n"
        "def forward_ref(x: 'LateResolved') -> \"AlsoLate\":\n"  # both quote styles
        "    return x\n"
    ),
    "app/models.py": (
        "from typing import ClassVar, Optional\n"
        "\n"
        "class User:\n"
        "    id: int\n"
        "    name: str\n"
        "    shared: ClassVar[int] = 0\n"
        "\n"
        "    def __init__(self, store: Store):\n"
        "        self._store = store\n"
        "        self._count: int = 0\n"
        "\n"
        "    def save(self) -> None:\n"
        "        pass\n"
        "\n"
        "class Store:\n"
        "    pass\n"
    ),
    "app/services/__init__.py": "",
    "app/services/handlers.py": (
        "from app.models import User\n"
        "\n"
        "def handle_user(u: User) -> User:\n"
        "    return u\n"
        "\n"
        "def build_user(name: str) -> User:\n"
        "    return User(Store())\n"
    ),
    ".venv/site-packages/ignored.py": "class ShouldBeIgnored: pass\n",
}


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _snapshot(lookup: StructuralIndexLookup) -> dict:
    """Extract a comparable dict snapshot of the lookup's content."""
    return {
        "class_names": sorted(lookup._all_class_names),
        "function_names": sorted(lookup._all_function_names),
        "method_names": sorted(lookup._all_method_names),
        "classes": {
            name: [
                {
                    "file": c.file,
                    "bases": sorted(c.bases),
                    "methods": {
                        m: (info.return_type) for m, info in c.methods.items()
                    },
                    "fields": dict(c.fields),
                }
                for c in entries
            ]
            for name, entries in lookup.classes.items()
        },
        "functions": {
            name: [
                {"file": f.file, "params": list(f.params), "return_type": f.return_type}
                for f in entries
            ]
            for name, entries in lookup.functions.items()
        },
    }


def test_augment_from_source_dir_parity(tmp_path: Path) -> None:
    _write_tree(tmp_path, FIXTURE_FILES)

    ast_lookup = StructuralIndexLookup(index_text="")
    ast_added = ast_lookup.augment_from_source_dir(str(tmp_path))

    ts_lookup = StructuralIndexLookup(index_text="")
    ts_added = _ts_inference.augment_from_source_dir(ts_lookup, str(tmp_path))

    assert ast_added == ts_added, "same number of new classes"
    assert _snapshot(ast_lookup) == _snapshot(ts_lookup)


def test_venv_excluded_in_both(tmp_path: Path) -> None:
    _write_tree(tmp_path, FIXTURE_FILES)
    ast_lookup = StructuralIndexLookup(index_text="")
    ast_lookup.augment_from_source_dir(str(tmp_path))
    assert "ShouldBeIgnored" not in ast_lookup._all_class_names

    ts_lookup = StructuralIndexLookup(index_text="")
    _ts_inference.augment_from_source_dir(ts_lookup, str(tmp_path))
    assert "ShouldBeIgnored" not in ts_lookup._all_class_names
