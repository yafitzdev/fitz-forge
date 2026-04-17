# fitz_forge/planning/agent/_ts_indexer.py
"""Tree-sitter implementations of indexer.py's ast-dependent extractors.

Entry points match the ast versions one-for-one and return the same
string output so callers just swap implementations based on engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..validation.grounding._ts_inference import (
    _class_body,
    _function_is_async,
    _function_name,
    _returns_annotation,
    _rightmost_attribute_name,
    _unwrap_decorated,
    iter_all_classes,
    iter_class_methods,
    unparse_annotation,
)
from ..validation.grounding._ts_parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node


# Reuse the constants from the ast module — they're data, not code.
def _const(name: str):
    from . import indexer

    return getattr(indexer, name)


def _ast_name(node: "Node | None") -> str:
    """Human-readable name from a tree-sitter node."""
    if node is None:
        return ""
    if node.type == "identifier":
        return node.text.decode("utf-8")
    if node.type == "attribute":
        # Recurse into value, then append .attr
        value = next((c for c in node.children if c.type in ("identifier", "attribute")), None)
        idents = [c for c in node.children if c.type == "identifier"]
        if value is None or value.type == "identifier":
            if idents:
                return ".".join(i.text.decode("utf-8") for i in idents)
            return "?"
        # value is another attribute — recurse
        left = _ast_name(value)
        attr_ident = idents[-1] if idents else None
        if attr_ident is not None:
            return f"{left}.{attr_ident.text.decode('utf-8')}" if left else attr_ident.text.decode("utf-8")
        return left or "?"
    if node.type == "subscript":
        # [value][slice] — use value
        value = next((c for c in node.children if c.is_named), None)
        return _ast_name(value)
    return "?"


def _extract_key_decorators(class_or_func_node: "Node") -> list[str]:
    """Extract recognised decorator names from a decorated_definition wrapper."""
    key = _const("_KEY_DECORATORS")
    result: list[str] = []
    # If this node was unwrapped from decorated_definition, its parent holds decorators
    parent = class_or_func_node.parent
    if parent is None or parent.type != "decorated_definition":
        return result
    for c in parent.children:
        if c.type != "decorator":
            continue
        body = next((x for x in c.children if x.is_named), None)
        name = None
        if body is None:
            continue
        if body.type == "identifier":
            name = body.text.decode("utf-8")
        elif body.type == "attribute":
            name = _rightmost_attribute_name(body)
        elif body.type == "call":
            callee = next(
                (x for x in body.children if x.is_named and x.type != "argument_list"),
                None,
            )
            if callee is None:
                continue
            if callee.type == "identifier":
                name = callee.text.decode("utf-8")
            elif callee.type == "attribute":
                name = _rightmost_attribute_name(callee)
        if name and name in key:
            result.append(name)
    return result


def _iter_top_level(root: "Node"):
    """Yield each top-level definition, unwrapping decorated wrappers.

    Mirrors ``ast.iter_child_nodes(tree)`` — returns direct children of
    the module only.
    """
    for c in root.children:
        if c.type == "decorated_definition":
            inner = _unwrap_decorated(c)
            if inner.type in ("function_definition", "class_definition"):
                yield inner
        else:
            yield c


def _all_param_names_except_self(func_def: "Node") -> list[str]:
    """Positional-or-keyword params only, matching ast ``node.args.args``.

    Stops at the ``keyword_separator`` (``*`` or ``list_splat_pattern``) so
    kwonly args — which ast buckets separately into ``kwonlyargs`` — are
    excluded, preserving parity with the ast indexer.
    """
    params_node = next((c for c in func_def.children if c.type == "parameters"), None)
    if params_node is None:
        return []
    out: list[str] = []
    for p in params_node.children:
        if p.type in ("keyword_separator", "list_splat_pattern", "dictionary_splat_pattern"):
            break
        name: str | None = None
        if p.type == "identifier":
            name = p.text.decode("utf-8")
        elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                name = ident.text.decode("utf-8")
        if name and name != "self":
            out.append(name)
    return out


def _formatted_param(p: "Node") -> str | None:
    """Return formatted ``name[: Ann]`` string for a parameter node, or None to skip."""
    if p.type == "identifier":
        name = p.text.decode("utf-8")
        return None if name == "self" else name
    if p.type == "typed_parameter":
        ident = next((c for c in p.children if c.type == "identifier"), None)
        tnode = next((c for c in p.children if c.type == "type"), None)
        if ident is None:
            return None
        name = ident.text.decode("utf-8")
        if name == "self":
            return None
        if tnode is None:
            return name
        unparsed = unparse_annotation(tnode) or "?"
        return f"{name}: {unparsed}"
    if p.type == "default_parameter":
        ident = next((c for c in p.children if c.type == "identifier"), None)
        if ident is None:
            return None
        return ident.text.decode("utf-8")
    if p.type == "typed_default_parameter":
        ident = next((c for c in p.children if c.type == "identifier"), None)
        tnode = next((c for c in p.children if c.type == "type"), None)
        if ident is None:
            return None
        name = ident.text.decode("utf-8")
        if tnode is None:
            return name
        return f"{name}: {unparse_annotation(tnode) or '?'}"
    return None


def _module_docstring(root: "Node") -> str | None:
    """First real statement's string literal, if any.

    Mirrors ``ast.get_docstring(module)``: comments are ignored (ast
    never yields them), so we skip over ``comment`` nodes until we hit
    the first genuine statement, then check if it wraps a string literal.
    """
    for c in root.children:
        if not c.is_named or c.type == "comment":
            continue
        if c.type == "expression_statement":
            inner = next((x for x in c.children if x.is_named), None)
            if inner is not None and inner.type == "string":
                text = inner.text.decode("utf-8").strip()
                for q in ('"""', "'''", '"', "'"):
                    if text.startswith(q) and text.endswith(q) and len(text) >= 2 * len(q):
                        text = text[len(q) : -len(q)]
                        break
                first_line = text.strip().splitlines()[0] if text.strip() else ""
                return first_line
        return None
    return None


# ---------------------------------------------------------------------------
# Entry point 1: _extract_python (structural index)
# ---------------------------------------------------------------------------


def extract_python(content: str) -> str:
    from . import indexer

    tree = parse_python(content)
    if tree is None:
        return indexer._extract_python_regex(content)
    root = tree.root_node
    lines: list[str] = []

    doc = _module_docstring(root)
    if doc:
        lines.append(f'doc: "{doc}"')

    classes_out: list[str] = []
    for node in _iter_top_level(root):
        if node.type != "class_definition":
            continue
        cname_node = next((c for c in node.children if c.type == "identifier"), None)
        if cname_node is None:
            continue
        bases: list[str] = []
        args = next((c for c in node.children if c.type == "argument_list"), None)
        if args is not None:
            for c in args.children:
                if c.is_named:
                    bases.append(_ast_name(c))
        methods: list[str] = []
        fields: list[str] = []
        for m in iter_class_methods(node):
            mname = _function_name(m)
            if mname is None:
                continue
            m_str = mname
            ret_node = _returns_annotation(m)
            if ret_node is not None:
                ret = unparse_annotation(ret_node)
                if ret:
                    m_str += f" -> {ret}"
            methods.append(m_str)
        # Class-level annotated assignments (fields)
        body = _class_body(node)
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
                has_type = any(c.type == "type" for c in inner.children)
                if has_type:
                    fields.append(target.text.decode("utf-8"))

        cls_str = cname_node.text.decode("utf-8")
        if bases:
            cls_str += f"({', '.join(bases)})"
        decs = _extract_key_decorators(node)
        if decs:
            cls_str += f" [{', '.join(f'@{d}' for d in decs)}]"
        if methods:
            cls_str += f" [{', '.join(methods)}]"
        elif fields:
            cls_str += f" [{', '.join(fields)}]"
        classes_out.append(cls_str)
    if classes_out:
        lines.append(f"classes: {'; '.join(classes_out)}")

    # Top-level functions
    functions_out: list[str] = []
    for node in _iter_top_level(root):
        if node.type != "function_definition":
            continue
        fname = _function_name(node)
        if fname is None:
            continue
        params = _all_param_names_except_self(node)
        func_str = f"{fname}({', '.join(params)})"
        ret_node = _returns_annotation(node)
        if ret_node is not None:
            ret = unparse_annotation(ret_node)
            if ret:
                func_str += f" -> {ret}"
        decs = _extract_key_decorators(node)
        if decs:
            func_str += f" [{', '.join(f'@{d}' for d in decs)}]"
        functions_out.append(func_str)
    if functions_out:
        lines.append(f"functions: {', '.join(functions_out)}")

    # Imports — full tree walk, matches ast.walk
    imports: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "import_statement":
            for c in n.children:
                if c.type == "dotted_name":
                    imports.add(c.text.decode("utf-8"))
                elif c.type == "aliased_import":
                    orig = next((x for x in c.children if x.type == "dotted_name"), None)
                    if orig is not None:
                        imports.add(orig.text.decode("utf-8"))
        elif n.type == "import_from_statement":
            module_node = next(
                (c for c in n.children if c.type in ("dotted_name", "relative_import")),
                None,
            )
            if module_node is not None:
                if module_node.type == "relative_import":
                    inner = next(
                        (c for c in module_node.children if c.type == "dotted_name"),
                        None,
                    )
                    text = inner.text.decode("utf-8") if inner else ""
                else:
                    text = module_node.text.decode("utf-8")
                if text:
                    imports.add(text)
        elif n.type == "future_import_statement":
            # Tree-sitter special-cases ``from __future__ import X`` as a
            # distinct node type; ast still exposes it as ImportFrom with
            # ``module='__future__'``. Add the module so outputs match.
            imports.add("__future__")
        stack.extend(n.children)
    if imports:
        lines.append(f"imports: {', '.join(sorted(imports))}")

    # __all__ exports
    for node in _iter_top_level(root):
        if node.type != "expression_statement":
            continue
        inner = next((c for c in node.children if c.is_named), None)
        if inner is None or inner.type != "assignment":
            continue
        target = next((c for c in inner.children if c.is_named), None)
        if target is None or target.type != "identifier":
            continue
        if target.text.decode("utf-8") != "__all__":
            continue
        # Value
        saw_eq = False
        value = None
        for c in inner.children:
            if not c.is_named and c.type == "=":
                saw_eq = True
                continue
            if saw_eq and c.is_named:
                value = c
                break
        if value is None or value.type not in ("list", "tuple"):
            continue
        names: list[str] = []
        for elt in value.children:
            if elt.type == "string":
                # Strip quotes
                raw = elt.text.decode("utf-8")
                for q in ('"""', "'''", '"', "'"):
                    if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                        names.append(raw[len(q) : -len(q)])
                        break
        if names:
            lines.append(f"exports: {', '.join(names)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point 2: extract_method_flows
# ---------------------------------------------------------------------------


def extract_method_flows(content: str, min_lines: int) -> str:
    from . import indexer

    tree = parse_python(content)
    if tree is None:
        return ""
    root = tree.root_node
    results: list[str] = []

    for cls in _iter_top_level(root):
        if cls.type != "class_definition":
            continue
        cname_node = next((c for c in cls.children if c.type == "identifier"), None)
        cname = cname_node.text.decode("utf-8") if cname_node else "?"

        # component_types from __init__ assignments
        component_types: dict[str, str] = {}
        for method in iter_class_methods(cls):
            if _function_name(method) != "__init__":
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
                    if (
                        len(idents) == 2
                        and idents[0].text.decode("utf-8") == "self"
                        and idents[1].text.decode("utf-8").startswith("_")
                    ):
                        # Value node
                        saw_eq = False
                        value = None
                        for c in n.children:
                            if not c.is_named and c.type == "=":
                                saw_eq = True
                                continue
                            if saw_eq and c.is_named:
                                value = c
                                break
                        if value is not None and value.type == "call":
                            callee = next(
                                (
                                    c
                                    for c in value.children
                                    if c.is_named and c.type != "argument_list"
                                ),
                                None,
                            )
                            if callee is not None:
                                if callee.type == "identifier":
                                    component_types[idents[1].text.decode("utf-8")] = (
                                        callee.text.decode("utf-8")
                                    )
                                elif callee.type == "attribute":
                                    name = _rightmost_attribute_name(callee)
                                    if name:
                                        component_types[idents[1].text.decode("utf-8")] = name
                stack.extend(n.children)

        # Flow steps per complex method
        for method in iter_class_methods(cls):
            mname = _function_name(method)
            if mname is None or mname.startswith("__"):
                continue
            body_lines = method.end_point[0] - method.start_point[0]
            if body_lines < min_lines:
                continue
            steps = _extract_flow_steps(method, component_types)
            if len(steps) < 3:
                continue
            results.append(f"flow {cname}.{mname}(): " + " → ".join(steps))

    return "\n".join(results)


def _extract_flow_steps(method: "Node", component_types: dict[str, str]) -> list[str]:
    from . import indexer

    skip_methods = indexer._FLOW_SKIP_METHODS
    skip_objects = indexer._FLOW_SKIP_OBJECTS
    calls: list[tuple[int, str]] = []

    body = next((c for c in method.children if c.type == "block"), None)
    if body is None:
        return []
    stack = list(body.children)
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type != "call":
            continue
        line = n.start_point[0] + 1
        callee = next(
            (c for c in n.children if c.is_named and c.type != "argument_list"),
            None,
        )
        if callee is None:
            continue
        if callee.type == "attribute":
            idents = [c for c in callee.children if c.type == "identifier"]
            attr = idents[-1].text.decode("utf-8") if idents else ""
            if attr in skip_methods:
                continue
            # self._component.method()
            inner_attr = next((c for c in callee.children if c.type == "attribute"), None)
            if inner_attr is not None:
                inner_idents = [c for c in inner_attr.children if c.type == "identifier"]
                if (
                    len(inner_idents) == 2
                    and inner_idents[0].text.decode("utf-8") == "self"
                ):
                    component = inner_idents[1].text.decode("utf-8")
                    if component in skip_objects:
                        continue
                    comp_type = component_types.get(component, component.lstrip("_"))
                    calls.append((line, f"{comp_type}.{attr}()"))
                    continue
            # self.method()
            if len(idents) == 2 and idents[0].text.decode("utf-8") == "self":
                if not attr.startswith("__"):
                    calls.append((line, f"self.{attr}()"))
        elif callee.type == "identifier":
            name = callee.text.decode("utf-8")
            if name in skip_objects:
                continue
            if name[0].isupper() or name in (
                "run_constraints",
                "extract_features",
                "compress_results",
                "build_retrieval_profile",
            ):
                calls.append((line, f"{name}()"))

    seen: set[str] = set()
    steps: list[str] = []
    for _, call in sorted(calls):
        if call not in seen:
            seen.add(call)
            steps.append(call)
    return steps


# ---------------------------------------------------------------------------
# Entry point 3: _extract_full_imports
# ---------------------------------------------------------------------------


def extract_full_imports(content: str, file_path: str = "") -> set[str]:
    from . import indexer

    tree = parse_python(content)
    if tree is None:
        return indexer._extract_full_imports_regex(content)

    pkg = ""
    if file_path:
        parts = file_path.replace("\\", "/").split("/")
        if parts[-1] == "__init__.py":
            pkg = ".".join(parts[:-1])
        else:
            pkg = ".".join(parts[:-1])

    imports: set[str] = set()
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "import_statement":
            for c in n.children:
                if c.type == "dotted_name":
                    imports.add(c.text.decode("utf-8"))
                elif c.type == "aliased_import":
                    orig = next((x for x in c.children if x.type == "dotted_name"), None)
                    if orig is not None:
                        imports.add(orig.text.decode("utf-8"))
        elif n.type == "import_from_statement":
            # Determine relative level and module
            relative = next((c for c in n.children if c.type == "relative_import"), None)
            module_node = next((c for c in n.children if c.type == "dotted_name"), None)
            if relative is not None and pkg:
                # Only emit if the relative_import carries a module name.
                # Bare ``from . import X`` has ``node.module is None`` in
                # ast and contributes nothing — we mirror that by only
                # using ``inner_dotted`` inside the relative_import.
                inner_dotted = next((c for c in relative.children if c.type == "dotted_name"), None)
                if inner_dotted is not None:
                    dots_text = relative.text.decode("utf-8")
                    level = 0
                    for ch in dots_text:
                        if ch == ".":
                            level += 1
                        else:
                            break
                    mod = inner_dotted.text.decode("utf-8")
                    parent = pkg
                    for _ in range(level - 1):
                        dot = parent.rfind(".")
                        if dot >= 0:
                            parent = parent[:dot]
                        else:
                            break
                    imports.add(f"{parent}.{mod}")
            elif module_node is not None:
                imports.add(module_node.text.decode("utf-8"))
        elif n.type == "future_import_statement":
            imports.add("__future__")
        stack.extend(n.children)
    return imports


# ---------------------------------------------------------------------------
# Entry point 4: _extract_signatures_from_python
# ---------------------------------------------------------------------------


def extract_signatures_from_python(content: str) -> str:
    tree = parse_python(content)
    if tree is None:
        return ""
    root = tree.root_node
    lines: list[str] = []

    for node in _iter_top_level(root):
        if node.type == "class_definition":
            cname_node = next((c for c in node.children if c.type == "identifier"), None)
            if cname_node is None:
                continue
            cname = cname_node.text.decode("utf-8")
            bases: list[str] = []
            args = next((c for c in node.children if c.type == "argument_list"), None)
            if args is not None:
                for c in args.children:
                    if c.is_named:
                        bases.append(_ast_name(c))
            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"class {cname}{base_str}:")

            for m in iter_class_methods(node):
                mname = _function_name(m)
                if mname is None:
                    continue
                params_node = next((c for c in m.children if c.type == "parameters"), None)
                params: list[str] = []
                if params_node is not None:
                    for p in params_node.children:
                        if p.type in (
                            "keyword_separator",
                            "list_splat_pattern",
                            "dictionary_splat_pattern",
                        ):
                            break
                        formatted = _formatted_param(p)
                        if formatted:
                            params.append(formatted)
                ret_node = _returns_annotation(m)
                ret_str = ""
                if ret_node is not None:
                    unparsed = unparse_annotation(ret_node) or "?"
                    ret_str = f" -> {unparsed}"
                async_prefix = "async " if _function_is_async(m) else ""
                lines.append(f"  {async_prefix}{mname}({', '.join(params)}){ret_str}")
        elif node.type == "function_definition":
            fname = _function_name(node)
            if fname is None:
                continue
            params_node = next((c for c in node.children if c.type == "parameters"), None)
            params = []
            if params_node is not None:
                for p in params_node.children:
                    if p.type in (
                        "keyword_separator",
                        "list_splat_pattern",
                        "dictionary_splat_pattern",
                    ):
                        break
                    formatted = _formatted_param(p)
                    if formatted:
                        params.append(formatted)
            ret_node = _returns_annotation(node)
            ret_str = ""
            if ret_node is not None:
                unparsed = unparse_annotation(ret_node) or "?"
                ret_str = f" -> {unparsed}"
            async_prefix = "async " if _function_is_async(node) else ""
            lines.append(f"{async_prefix}{fname}({', '.join(params)}){ret_str}")
    return "\n".join(lines)
