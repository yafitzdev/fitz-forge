# fitz_forge/planning/pipeline/tools/codebase_tools.py
"""
Codebase lookup tools for tool-assisted artifact building.

These tools let the LLM look up real method signatures, class attributes,
and source code during artifact generation — preventing fabrication by
grounding every reference in the actual codebase.

All tools are pure Python (no LLM calls). They query the structural index
and source code that was already gathered by the agent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fitz_forge.planning.validation.grounding import StructuralIndexLookup
from fitz_forge.planning.validation.grounding.inference import (
    _class_body,
    _function_name,
    _returns_annotation,
    iter_all_classes,
    iter_class_methods,
    unparse_annotation,
)
from fitz_forge.planning.validation.grounding.parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node

logger = logging.getLogger(__name__)


def _find_class(src: str, class_name: str) -> "Node | None":
    """Return the class_definition node with ``.name == class_name``, or None."""
    tree = parse_python(src)
    if tree is None:
        return None
    for cls in iter_all_classes(tree.root_node):
        name_node = next((c for c in cls.children if c.type == "identifier"), None)
        if name_node is not None and name_node.text.decode("utf-8") == class_name:
            return cls
    return None


def _format_method_sig(method: "Node") -> str:
    """Signature line for a function_definition."""
    params: list[str] = []
    params_node = next((c for c in method.children if c.type == "parameters"), None)
    if params_node is not None:
        for p in params_node.children:
            if p.type == "identifier":
                name = p.text.decode("utf-8")
                if name != "self":
                    params.append(name)
            elif p.type == "typed_parameter":
                ident = next((c for c in p.children if c.type == "identifier"), None)
                tnode = next((c for c in p.children if c.type == "type"), None)
                if ident is None:
                    continue
                name = ident.text.decode("utf-8")
                if name == "self":
                    continue
                entry = name
                if tnode is not None:
                    unparsed = unparse_annotation(tnode)
                    if unparsed:
                        entry += f": {unparsed}"
                params.append(entry)
            elif p.type == "default_parameter":
                ident = next((c for c in p.children if c.type == "identifier"), None)
                if ident is None:
                    continue
                name = ident.text.decode("utf-8")
                saw_eq = False
                default_node = None
                for c in p.children:
                    if not c.is_named and c.type == "=":
                        saw_eq = True
                        continue
                    if saw_eq and c.is_named:
                        default_node = c
                        break
                if name != "self":
                    suffix = f" = {default_node.text.decode('utf-8')}" if default_node else ""
                    params.append(f"{name}{suffix}")
            elif p.type == "typed_default_parameter":
                ident = next((c for c in p.children if c.type == "identifier"), None)
                tnode = next((c for c in p.children if c.type == "type"), None)
                if ident is None:
                    continue
                name = ident.text.decode("utf-8")
                if name == "self":
                    continue
                entry = name
                if tnode is not None:
                    unparsed = unparse_annotation(tnode)
                    if unparsed:
                        entry += f": {unparsed}"
                saw_eq = False
                default_node = None
                for c in p.children:
                    if not c.is_named and c.type == "=":
                        saw_eq = True
                        continue
                    if saw_eq and c.is_named:
                        default_node = c
                        break
                if default_node is not None:
                    entry += f" = {default_node.text.decode('utf-8')}"
                params.append(entry)

    sig = f"{_function_name(method)}({', '.join(params)})"
    ret_node = _returns_annotation(method)
    if ret_node is not None:
        unparsed = unparse_annotation(ret_node)
        if unparsed:
            sig += f" -> {unparsed}"
    return sig


def _lookup_method(src: str, class_name: str, method_name: str) -> str | None:
    """Return formatted signature string or None if not found."""
    cls = _find_class(src, class_name)
    if cls is None:
        return None
    for m in iter_class_methods(cls):
        if _function_name(m) == method_name:
            return f"{class_name}.{_format_method_sig(m)}"
    return None


def _lookup_class(src: str, class_name: str) -> str | None:
    """Return a multiline description of the class, or None if not found."""
    cls = _find_class(src, class_name)
    if cls is None:
        return None

    parts: list[str] = []

    # Bases
    args = next((c for c in cls.children if c.type == "argument_list"), None)
    bases: list[str] = []
    if args is not None:
        for c in args.children:
            if c.is_named:
                bases.append(c.text.decode("utf-8"))
    if bases:
        parts.append(f"class {class_name}({', '.join(bases)})")
    else:
        parts.append(f"class {class_name}")

    # Instance attrs from __init__ / _init_components
    attrs: list[str] = []
    for method in iter_class_methods(cls):
        mname = _function_name(method)
        if mname not in ("__init__", "_init_components"):
            continue
        body = next((c for c in method.children if c.type == "block"), None)
        if body is None:
            continue
        stack = list(body.children)
        while stack:
            n = stack.pop()
            if n.type == "assignment":
                target = next((c for c in n.children if c.is_named), None)
                if target is None or target.type != "attribute":
                    stack.extend(n.children)
                    continue
                idents = [c for c in target.children if c.type == "identifier"]
                if len(idents) != 2 or idents[0].text.decode("utf-8") != "self":
                    stack.extend(n.children)
                    continue
                attr_name = idents[1].text.decode("utf-8")
                saw_eq = False
                value = None
                for c in n.children:
                    if not c.is_named and c.type == "=":
                        saw_eq = True
                        continue
                    if saw_eq and c.is_named:
                        value = c
                        break
                rhs = ""
                if value is not None and value.type == "call":
                    callee = next(
                        (c for c in value.children if c.is_named and c.type != "argument_list"),
                        None,
                    )
                    if callee is not None:
                        if callee.type == "identifier":
                            rhs = callee.text.decode("utf-8")
                        elif callee.type == "attribute":
                            idents2 = [c for c in callee.children if c.type == "identifier"]
                            if idents2:
                                rhs = idents2[-1].text.decode("utf-8")
                attrs.append(
                    f"  self.{attr_name} = {rhs}(...)" if rhs else f"  self.{attr_name}"
                )
            stack.extend(n.children)
    if attrs:
        parts.append("Attributes:")
        parts.extend(attrs[:20])

    # Class-level annotated fields (Pydantic models, dataclasses)
    fields: list[str] = []
    body = _class_body(cls)
    if body is not None:
        for stmt in body.children:
            if stmt.type != "expression_statement":
                continue
            inner = next((c for c in stmt.children if c.is_named), None)
            if inner is None or inner.type != "assignment":
                continue
            target = next((c for c in inner.children if c.is_named), None)
            if target is None or target.type != "identifier":
                continue
            tnode = next((c for c in inner.children if c.type == "type"), None)
            if tnode is None:
                continue
            ann = unparse_annotation(tnode) or "?"
            fields.append(f"  {target.text.decode('utf-8')}: {ann}")
    if fields:
        parts.append("Fields:")
        parts.extend(fields[:20])

    # Methods (excluding dunders)
    methods_lines: list[str] = []
    for m in iter_class_methods(cls):
        mname = _function_name(m)
        if mname is None or mname.startswith("__"):
            continue
        methods_lines.append(f"  {_format_method_sig(m)}")
    if methods_lines:
        parts.append("Methods:")
        parts.extend(methods_lines[:15])

    return "\n".join(parts)


def make_codebase_tools(
    structural_index: str,
    file_contents: dict[str, str],
    source_dir: str | None = None,
) -> list:
    """Create codebase lookup tools bound to the gathered context.

    Returns a list of callables suitable for generate_with_tools().
    """
    lookup = StructuralIndexLookup(structural_index)

    # Build a source pool: file_contents + disk fallback
    _source_pool = dict(file_contents) if file_contents else {}

    def _find_source(class_name: str) -> str | None:
        """Find source code containing a class definition."""
        class_marker = f"class {class_name}"
        for _, content in _source_pool.items():
            if class_marker in content:
                return content
        if source_dir:
            cn_lower = class_name.lower()
            for py in Path(source_dir).rglob("*.py"):
                parts_str = str(py)
                if (
                    ".venv" in parts_str
                    or "__pycache__" in parts_str
                    or "site-packages" in parts_str
                ):
                    continue
                stem = py.stem.lower()
                if len(stem) >= 4 and (cn_lower in stem or stem in cn_lower):
                    try:
                        src = py.read_text(encoding="utf-8", errors="replace")
                        if f"class {class_name}" in src:
                            return src
                    except OSError:
                        continue
        return None

    def _strip_module(name: str) -> str:
        """Strip module path from a class/function name."""
        if "." in name:
            return name.rsplit(".", 1)[-1]
        return name

    # ------------------------------------------------------------------
    # Tool 1: lookup_method
    # ------------------------------------------------------------------
    def lookup_method(class_name: str, method_name: str) -> str:
        """Look up the full signature of a method on a class in the codebase."""
        class_name = _strip_module(class_name)
        method_name = _strip_module(method_name)

        # Try source-based resolution first (most accurate)
        src = _find_source(class_name)
        if src:
            hit = _lookup_method(src, class_name, method_name)
            if hit:
                return hit

        # Fall back to structural index
        if lookup.class_has_method(class_name, method_name):
            for cls in lookup.find_classes(class_name):
                if method_name in cls.methods:
                    ret = cls.methods[method_name].return_type
                    ret_str = f" -> {ret}" if ret else ""
                    return f"{class_name}.{method_name}(...){ret_str} (params not available from index)"

        # Check top-level functions
        funcs = lookup.find_function(method_name)
        if funcs:
            f = funcs[0]
            params = ", ".join(f.params) if f.params else ""
            ret = f" -> {f.return_type}" if f.return_type else ""
            return f"{method_name}({params}){ret} [top-level function in {f.file}]"

        return f"METHOD NOT FOUND: {class_name}.{method_name}() does not exist in the codebase. Do not use it."

    # ------------------------------------------------------------------
    # Tool 2: lookup_class
    # ------------------------------------------------------------------
    def lookup_class(class_name: str) -> str:
        """Look up a class: its methods, instance attributes, and base classes."""
        class_name = _strip_module(class_name)

        src = _find_source(class_name)
        if src:
            hit = _lookup_class(src, class_name)
            if hit:
                return hit

        # Fall back to structural index
        parts: list[str] = []
        cls = lookup.find_class(class_name)
        if cls:
            parts.append(f"class {class_name} (from structural index, {cls.file})")
            if cls.bases:
                parts.append(f"  Bases: {', '.join(cls.bases)}")
            if cls.methods:
                parts.append("  Methods:")
                for name, m in cls.methods.items():
                    ret = f" -> {m.return_type}" if m.return_type else ""
                    parts.append(f"    {name}{ret}")
            return "\n".join(parts)

        return f"CLASS NOT FOUND: {class_name} does not exist in the codebase."

    # ------------------------------------------------------------------
    # Tool 3: check_exists
    # ------------------------------------------------------------------
    def check_exists(symbol_name: str) -> str:
        """Check if a class, method, or function exists anywhere in the codebase."""
        if lookup.class_exists(symbol_name):
            cls = lookup.find_class(symbol_name)
            return f"EXISTS: class {symbol_name} in {cls.file}"

        if lookup.function_exists(symbol_name):
            funcs = lookup.find_function(symbol_name)
            return f"EXISTS: function {symbol_name} in {funcs[0].file}"

        if lookup.method_exists_anywhere(symbol_name):
            for cls_name, cls_list in lookup.classes.items():
                for cls in cls_list:
                    if symbol_name in cls.methods:
                        return f"EXISTS: method {cls_name}.{symbol_name}() in {cls.file}"

        return f"DOES NOT EXIST: no class, function, or method named '{symbol_name}' found in the codebase. Do not use it."

    # ------------------------------------------------------------------
    # Tool 4: read_method_source
    # ------------------------------------------------------------------
    def read_method_source(class_name: str, method_name: str) -> str:
        """Read the actual source code of a method (up to 2000 chars)."""
        class_name = _strip_module(class_name)
        method_name = _strip_module(method_name)
        src = _find_source(class_name)
        if not src:
            return f"SOURCE NOT AVAILABLE for {class_name}"

        cls = _find_class(src, class_name)
        if cls is None:
            return f"CLASS {class_name} NOT FOUND in source"

        for m in iter_class_methods(cls):
            if _function_name(m) != method_name:
                continue
            # Extract source lines by tree-sitter byte range
            lines = src.split("\n")
            start = m.start_point[0]  # 0-indexed
            end_row = m.end_point[0]
            end_col = m.end_point[1]
            end = end_row if end_col == 0 else end_row + 1
            method_src = "\n".join(lines[start:end])
            if len(method_src) > 2000:
                method_src = method_src[:2000] + "\n... (truncated)"
            return method_src

        return f"METHOD {method_name} NOT FOUND on {class_name}"

    logger.info(
        f"Created 4 codebase tools (index: {len(lookup.classes)} classes, "
        f"{len(lookup.functions)} functions, sources: {len(_source_pool)} files)"
    )

    return [lookup_method, lookup_class, check_exists, read_method_source]
