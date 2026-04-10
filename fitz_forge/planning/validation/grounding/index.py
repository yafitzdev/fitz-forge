# fitz_forge/planning/validation/grounding/index.py
"""Structural index of the target codebase.

`StructuralIndexLookup` is the shared "what exists" registry consulted by
both the per-artifact grounding check and the plan-level closure check.

Two sources:
    1. The compact structural index text produced by the retrieval agent
       (parsed from markdown-ish headers).
    2. Full disk scan via `augment_from_source_dir` — the authoritative
       source used by the V2 scorer, closure check, and benchmarks.

The index tracks classes (methods + fields + bases), top-level functions
(params + return type), and provides MRO-aware lookups.
"""

from __future__ import annotations

import ast
import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .inference import (
    extract_class_fields,
    extract_type_name,
    infer_return_type,
    unparse_annotation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indexed dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IndexedMethod:
    name: str
    return_type: str | None = None


@dataclass
class IndexedClass:
    name: str
    file: str
    bases: list[str] = field(default_factory=list)
    methods: dict[str, IndexedMethod] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)  # field_name -> type_name
    decorators: list[str] = field(default_factory=list)


@dataclass
class IndexedFunction:
    name: str
    file: str
    params: list[str] = field(default_factory=list)
    return_type: str | None = None


# ---------------------------------------------------------------------------
# Regex for parsing the compact structural-index text
# ---------------------------------------------------------------------------

_CLASS_RE = re.compile(
    r"([A-Za-z_]\w*)"  # class name
    r"(?:\(([^)]*)\))?"  # optional bases
    r"(?:\s*\[([^\]]*)\])?"  # optional first bracket (decorators or methods)
    r"(?:\s*\[([^\]]*)\])?"  # optional second bracket
)

_FUNC_RE = re.compile(
    r"([A-Za-z_]\w*)"  # function name
    r"\(([^)]*)\)"  # params
    r"(?:\s*->\s*([^[,]+))?"  # optional return type
)


# ---------------------------------------------------------------------------
# StructuralIndexLookup
# ---------------------------------------------------------------------------


class StructuralIndexLookup:
    """Parsed structural index for programmatic symbol queries.

    Methods look up classes/functions/methods/fields with MRO awareness.
    `augment_from_source_dir` fills in the full codebase by direct AST
    walking of every .py file in the target source tree.
    """

    def __init__(self, index_text: str):
        self.classes: dict[str, list[IndexedClass]] = {}
        self.functions: dict[str, list[IndexedFunction]] = {}
        self._all_method_names: set[str] = set()
        self._all_class_names: set[str] = set()
        self._all_function_names: set[str] = set()
        self._parse(index_text)

    # ------------------------------------------------------------------
    # Index text parsing (retrieval agent output)
    # ------------------------------------------------------------------

    def _parse(self, text: str) -> None:
        current_file = ""
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("## "):
                current_file = line[3:].strip()
                continue
            if not current_file:
                continue
            if line.startswith("classes: "):
                self._parse_classes(line[9:], current_file)
            elif line.startswith("functions: "):
                self._parse_functions(line[11:], current_file)

    def _parse_classes(self, text: str, file: str) -> None:
        for cls_text in text.split("; "):
            cls_text = cls_text.strip()
            if not cls_text:
                continue
            m = _CLASS_RE.match(cls_text)
            if not m:
                continue
            name = m.group(1)
            bases = [b.strip() for b in m.group(2).split(",") if b.strip()] if m.group(2) else []
            decorators: list[str] = []
            methods: dict[str, IndexedMethod] = {}
            for bracket in (m.group(3), m.group(4)):
                if not bracket:
                    continue
                bracket = bracket.strip()
                if bracket.startswith("@"):
                    decorators = [d.strip().lstrip("@") for d in bracket.split(",")]
                else:
                    for method_str in bracket.split(", "):
                        method_str = method_str.strip()
                        if not method_str:
                            continue
                        if " -> " in method_str:
                            mname, ret = method_str.split(" -> ", 1)
                            methods[mname.strip()] = IndexedMethod(mname.strip(), ret.strip())
                        else:
                            methods[method_str] = IndexedMethod(method_str)
            cls = IndexedClass(name, file, bases, methods, {}, decorators)
            self.classes.setdefault(name, []).append(cls)
            self._all_class_names.add(name)
            for mname in methods:
                self._all_method_names.add(mname)

    def _parse_functions(self, text: str, file: str) -> None:
        for func_text in text.split(", "):
            func_text = func_text.strip()
            if not func_text:
                continue
            m = _FUNC_RE.match(func_text)
            if not m:
                continue
            name = m.group(1)
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            ret = m.group(3).strip() if m.group(3) else None
            func = IndexedFunction(name, file, params, ret)
            self.functions.setdefault(name, []).append(func)
            self._all_function_names.add(name)

    # ------------------------------------------------------------------
    # Direct lookups
    # ------------------------------------------------------------------

    def find_class(self, name: str) -> IndexedClass | None:
        entries = self.classes.get(name, [])
        return entries[0] if entries else None

    def find_classes(self, name: str) -> list[IndexedClass]:
        return self.classes.get(name, [])

    def find_function(self, name: str) -> list[IndexedFunction]:
        return self.functions.get(name, [])

    def class_exists(self, name: str) -> bool:
        return name in self._all_class_names

    def function_exists(self, name: str) -> bool:
        return name in self._all_function_names

    def method_exists_anywhere(self, method_name: str) -> bool:
        return method_name in self._all_method_names

    def function_params(self, name: str) -> list[str] | None:
        funcs = self.functions.get(name, [])
        return funcs[0].params if funcs else None

    def suggest_method(self, name: str) -> list[str]:
        return difflib.get_close_matches(name, self._all_method_names, n=3, cutoff=0.6)

    def suggest_class(self, name: str) -> list[str]:
        return difflib.get_close_matches(name, self._all_class_names, n=3, cutoff=0.6)

    def suggest_function(self, name: str) -> list[str]:
        return difflib.get_close_matches(name, self._all_function_names, n=3, cutoff=0.6)

    # ------------------------------------------------------------------
    # MRO-aware lookups
    # ------------------------------------------------------------------

    def class_has_method(self, class_name: str, method_name: str) -> bool:
        """Return True if the class or any of its ancestors has the method."""
        return self._walk_mro_for(class_name, method_name, "methods")

    def class_has_field(self, class_name: str, field_name: str) -> bool:
        """Return True if the class or any ancestor has the field.

        Falls back to checking as a method (for property-decorated fields
        and the permissive case where the index doesn't track a field).
        """
        if self._walk_mro_for(class_name, field_name, "fields"):
            return True
        # Properties/computed attrs appear as methods in the index.
        return self._walk_mro_for(class_name, field_name, "methods")

    def _walk_mro_for(
        self,
        class_name: str,
        member: str,
        attr: str,  # "methods" or "fields"
        _seen: set[str] | None = None,
    ) -> bool:
        """Recursively check class + bases for a method or field."""
        if _seen is None:
            _seen = set()
        if class_name in _seen:
            return False
        _seen.add(class_name)
        classes = self.classes.get(class_name, [])
        for cls in classes:
            if member in getattr(cls, attr, {}):
                return True
            for base in cls.bases:
                base_clean = base.split("[")[0].strip()  # strip generics
                if base_clean in _seen:
                    continue
                if self._walk_mro_for(base_clean, member, attr, _seen):
                    return True
        return False

    # ------------------------------------------------------------------
    # Augmentation from source directory
    # ------------------------------------------------------------------

    def augment_from_source_dir(self, source_dir: str) -> int:
        """Walk a source directory and enrich the index from real AST.

        Two passes:
            1. Collect all classes and functions (names, structure).
            2. Re-visit every function and try to infer its return type
               via body / yields / docstring — the docstring strategy
               needs the full class list from pass 1 to verify candidates.

        Returns the number of new classes added.
        """
        root = Path(source_dir)
        if not root.is_dir():
            return 0

        # Pass 1: collect
        parsed_files: list[tuple[str, ast.Module]] = []
        added = 0
        for py_file in root.rglob("*.py"):
            rel = str(py_file.relative_to(root)).replace("\\", "/")
            if ".venv" in rel or "__pycache__" in rel:
                continue
            try:
                text = py_file.read_bytes()[:200_000].decode("utf-8", errors="replace")
                tree = ast.parse(text)
            except (SyntaxError, OSError):
                continue
            parsed_files.append((rel, tree))
            added += self._absorb_file_pass1(rel, tree)

        # Pass 2: enrich function return types using full class list
        known_classes = set(self._all_class_names)
        for rel, tree in parsed_files:
            self._absorb_file_pass2(rel, tree, known_classes)

        return added

    def _absorb_file_pass1(self, rel: str, tree: ast.Module) -> int:
        """Pass 1: add classes (methods + fields + bases) and functions (params only)."""
        added = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._absorb_class(rel, node)
                added += 1
            elif isinstance(node, ast.FunctionDef) and getattr(node, "col_offset", 1) == 0:
                name = node.name
                if name in self._all_function_names:
                    continue
                params = [a.arg for a in node.args.args]
                func = IndexedFunction(name, rel, params, None)  # return type later
                self.functions.setdefault(name, []).append(func)
                self._all_function_names.add(name)
        return added

    def _absorb_file_pass2(
        self, rel: str, tree: ast.Module, known_classes: set[str]
    ) -> None:
        """Pass 2: infer return types for functions that have no annotation."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if getattr(node, "col_offset", 1) != 0:
                continue
            # Find the matching IndexedFunction we stored in pass 1
            funcs = self.functions.get(node.name, [])
            for f in funcs:
                if f.file == rel and f.return_type is None:
                    ret = infer_return_type(node, known_classes)
                    if ret:
                        f.return_type = ret
                    break

    def _absorb_class(self, rel: str, node: ast.ClassDef) -> None:
        """Absorb one class node: methods, fields, bases."""
        name = node.name
        methods: dict[str, IndexedMethod] = {}
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mname = child.name
                if mname.startswith("__") and mname != "__init__":
                    continue
                ret = unparse_annotation(child.returns)
                if ret is None:
                    # Try body inference for unannotated methods
                    from .inference import _infer_return_from_body, _infer_return_from_yields
                    ret = _infer_return_from_body(child) or _infer_return_from_yields(child)
                methods[mname] = IndexedMethod(mname, ret)

        fields = extract_class_fields(node)
        bases = [
            extract_type_name(b) or ""
            for b in node.bases
        ]
        bases = [b for b in bases if b]

        if name in self._all_class_names:
            # Class already indexed — merge new methods/fields
            for existing_cls in self.classes.get(name, []):
                for mname, minfo in methods.items():
                    if mname not in existing_cls.methods:
                        existing_cls.methods[mname] = minfo
                        self._all_method_names.add(mname)
                for fname, ftype in fields.items():
                    existing_cls.fields.setdefault(fname, ftype)
                for b in bases:
                    if b not in existing_cls.bases:
                        existing_cls.bases.append(b)
        else:
            cls = IndexedClass(name, rel, bases, methods, fields, [])
            self.classes.setdefault(name, []).append(cls)
            self._all_class_names.add(name)
            for mname in methods:
                self._all_method_names.add(mname)
