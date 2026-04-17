# fitz_forge/planning/pipeline/tools/_ts_codebase_tools.py
"""Tree-sitter implementations of codebase_tools.py's ast-dependent helpers.

Each function returns the formatted string directly — mirroring the
ast path's output so the caller's routing is a simple string swap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...validation.grounding._ts_inference import (
    _class_body,
    _function_is_async,
    _function_name,
    _returns_annotation,
    _unwrap_decorated,
    iter_all_classes,
    iter_class_methods,
    unparse_annotation,
)
from ...validation.grounding._ts_parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node


def _find_class(src: str, class_name: str):
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
    """Signature line for a function_definition. Mirrors the ast formatter."""
    params: list[str] = []
    defaults_text: dict[str, str] = {}
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
                # Default value is the last named child after ``=``
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
                # default after ``=``
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


def lookup_method(src: str, class_name: str, method_name: str) -> str | None:
    """Return formatted signature string or None if not found."""
    cls = _find_class(src, class_name)
    if cls is None:
        return None
    for m in iter_class_methods(cls):
        if _function_name(m) == method_name:
            return f"{class_name}.{_format_method_sig(m)}"
    return None


def lookup_class(src: str, class_name: str) -> str | None:
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
                # target must be attribute(self, X)
                target = next((c for c in n.children if c.is_named), None)
                if target is None or target.type != "attribute":
                    stack.extend(n.children)
                    continue
                idents = [c for c in target.children if c.type == "identifier"]
                if len(idents) != 2 or idents[0].text.decode("utf-8") != "self":
                    stack.extend(n.children)
                    continue
                attr_name = idents[1].text.decode("utf-8")
                # RHS
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
                attrs.append(f"  self.{attr_name} = {rhs}(...)" if rhs else f"  self.{attr_name}")
            stack.extend(n.children)
    if attrs:
        parts.append("Attributes:")
        parts.extend(attrs[:20])

    # Class-level annotated fields (expression_statement → assignment with type)
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
