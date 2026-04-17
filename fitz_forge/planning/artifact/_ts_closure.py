# fitz_forge/planning/artifact/_ts_closure.py
"""Tree-sitter implementation of closure.py's ast-using entry points.

Ports:
  - ``extract_references``: walks an artifact and emits Reference objects
    with full per-scope type tracking (parameter annotations, local
    assignments, iterator/awaitable propagation).
  - ``extract_provides``: returns the SymbolRef → Signature map for
    every top-level function/method/class the artifact defines.
  - ``load_target_self_attrs``: disk-loaded __init__ self-attr map.

Routed to from ``closure.py`` when ``grounding.index.get_engine() ==
"tree_sitter"``. The ast implementations stay in closure.py unchanged
so ``set_engine("ast")`` preserves the original code path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from ..validation.grounding._ts_inference import (
    _callable_of,
    _class_body,
    _extract_param_names,
    _function_is_async,
    _function_name,
    _rightmost_attribute_name,
    _unwrap_decorated,
    extract_init_self_attrs,
    extract_type_name,
    iter_all_classes,
    iter_class_methods,
    unparse_annotation,
)
from ..validation.grounding._ts_parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..validation.grounding.index import StructuralIndexLookup
    from .closure import Reference, Signature, SymbolRef


# These live on closure.py and are imported lazily to avoid a cycle.
def _sym_refs():
    from .closure import Reference, Signature, SymbolRef

    return SymbolRef, Signature, Reference


def _skip_names():
    from .closure import _SKIP_NAMES

    return _SKIP_NAMES


def _stdlib_packages():
    from .closure import _STDLIB_PACKAGES

    return _STDLIB_PACKAGES


def _locator_return_type_fn():
    from .closure import _locator_return_type

    return _locator_return_type


def _classifiers():
    from .closure import (
        _is_async_iter_type,
        _is_awaitable_type,
        _looks_like_generator,
    )

    return _looks_like_generator, _is_async_iter_type, _is_awaitable_type


# ---------------------------------------------------------------------------
# Annotation walking — yield class-shaped identifiers in a type subtree
# ---------------------------------------------------------------------------


def _iter_annotation_class_names(type_node: "Node | None") -> Iterator[tuple[str, int]]:
    """Yield (class_name, line) for class-shaped identifiers in an annotation.

    Mirrors ``closure._iter_annotation_class_names``:
      - walk the full subtree
      - yield ``identifier`` nodes whose text is Capitalised
      - skip names in ``_SKIP_NAMES`` and single-letter uppercase
    """
    if type_node is None:
        return
    skip = _skip_names()
    stack: list[Node] = [type_node]
    while stack:
        n = stack.pop()
        if n.type == "identifier":
            name = n.text.decode("utf-8")
            if name[:1].isupper() and name not in skip and len(name) != 1:
                yield name, n.start_point[0] + 1
        stack.extend(n.children)


def _find_module_typevars(root: "Node") -> set[str]:
    """Return names assigned to TypeVar / ParamSpec / TypeVarTuple calls.

    Tree-sitter renders ``T = TypeVar("T")`` as:
        expression_statement → assignment → identifier = call(identifier("TypeVar"), ...)
    """
    out: set[str] = set()
    stack: list[Node] = [root]
    while stack:
        n = stack.pop()
        if n.type == "assignment":
            # Only plain name assignments
            target = next((c for c in n.children if c.is_named), None)
            if target is None or target.type != "identifier":
                stack.extend(n.children)
                continue
            rhs = None
            seen_eq = False
            for c in n.children:
                if not c.is_named and c.type == "=":
                    seen_eq = True
                    continue
                if seen_eq and c.is_named:
                    rhs = c
                    break
            if rhs is not None and rhs.type == "call":
                callee = _callable_of(rhs)
                if (
                    callee is not None
                    and callee.type == "identifier"
                    and callee.text.decode("utf-8") in ("TypeVar", "ParamSpec", "TypeVarTuple")
                ):
                    out.add(target.text.decode("utf-8"))
        stack.extend(n.children)
    return out


# ---------------------------------------------------------------------------
# Signature extraction
# ---------------------------------------------------------------------------


def _has_yield(func_def: "Node") -> bool:
    """True if the function body contains any yield node (matches ast.walk)."""
    body = next((c for c in func_def.children if c.type == "block"), None)
    if body is None:
        return False
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type == "yield":
            return True
        stack.extend(n.children)
    return False


def _returns_annotation(func_def: "Node") -> "Node | None":
    saw_params = False
    for c in func_def.children:
        if c.type == "parameters":
            saw_params = True
            continue
        if saw_params and c.type == "type":
            return c
    return None


def _sig_from_funcdef(func_def: "Node") -> "Signature":
    _, Signature, _ = _sym_refs()
    params_node = next((c for c in func_def.children if c.type == "parameters"), None)
    params: list[str] = []
    has_var_kw = False
    if params_node is not None:
        for p in params_node.children:
            if p.type == "identifier":
                name = p.text.decode("utf-8")
                if name != "self":
                    params.append(name)
            elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                ident = next((c for c in p.children if c.type == "identifier"), None)
                if ident is None:
                    continue
                name = ident.text.decode("utf-8")
                if name != "self":
                    params.append(name)
            elif p.type == "list_splat_pattern":
                ident = next((c for c in p.children if c.type == "identifier"), None)
                if ident is not None:
                    params.append(f"*{ident.text.decode('utf-8')}")
            elif p.type == "dictionary_splat_pattern":
                has_var_kw = True

    ret = None
    ret_node = _returns_annotation(func_def)
    if ret_node is not None:
        ret = unparse_annotation(ret_node)
    return Signature(
        params=params,
        return_type=ret,
        is_async=_function_is_async(func_def),
        is_generator=_has_yield(func_def),
        has_var_keywords=has_var_kw,
    )


# ---------------------------------------------------------------------------
# extract_provides
# ---------------------------------------------------------------------------


def _target_class_for_file(filename: str, lookup: "StructuralIndexLookup") -> str | None:
    candidates: list[tuple[str, int]] = []
    for cls_list in lookup.classes.values():
        for cls in cls_list:
            if cls.file == filename or filename.endswith(cls.file):
                candidates.append((cls.name, len(cls.methods)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


def extract_provides(
    content: str,
    filename: str,
    lookup: "StructuralIndexLookup",
) -> "dict[SymbolRef, Signature | None]":
    SymbolRef, _, _ = _sym_refs()
    out: "dict[SymbolRef, Signature | None]" = {}
    tree = parse_python(content)
    if tree is None:
        return out
    root = tree.root_node

    # Surgical rewrite: indented content whose top-level items include
    # a function_definition (matches ast's ``any(isinstance(n, FunctionDef)
    # for n in iter_child_nodes(tree))``)
    is_surgical = False
    if content and content.lstrip() != content:
        for c in root.children:
            inner = _unwrap_decorated(c) if c.type == "decorated_definition" else c
            if inner.type == "function_definition":
                is_surgical = True
                break

    target_class = _target_class_for_file(filename, lookup) if is_surgical else None

    for node in root.children:
        real = _unwrap_decorated(node) if node.type == "decorated_definition" else node
        if real.type == "function_definition":
            name = _function_name(real)
            if name is None:
                continue
            sig = _sig_from_funcdef(real)
            if target_class:
                out[SymbolRef(owner=target_class, name=name, kind="method")] = sig
                out[SymbolRef(owner=target_class, name=name, kind="field")] = sig
            else:
                out[SymbolRef(owner=None, name=name, kind="function")] = sig
        elif real.type == "class_definition":
            cname_node = next((c for c in real.children if c.type == "identifier"), None)
            if cname_node is None:
                continue
            cname = cname_node.text.decode("utf-8")
            out[SymbolRef(owner=cname, name=None, kind="class")] = None
            for m in iter_class_methods(real):
                mname = _function_name(m)
                if mname is None:
                    continue
                sig = _sig_from_funcdef(m)
                out[SymbolRef(owner=cname, name=mname, kind="method")] = sig
                out[SymbolRef(owner=cname, name=mname, kind="field")] = sig
            # Pydantic / dataclass fields at class level
            body = _class_body(real)
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
                        out[
                            SymbolRef(
                                owner=cname, name=target.text.decode("utf-8"), kind="field"
                            )
                        ] = None
    return out


# ---------------------------------------------------------------------------
# Reference collector (visitor) — tree-sitter version
# ---------------------------------------------------------------------------


class _TsReferenceCollector:
    """Tree-sitter mirror of closure._ReferenceCollector.

    Walks the tree in the same logical order as ast's generic_visit —
    depth-first, with per-function scope push/pop so parameter annotation
    bindings don't leak.
    """

    def __init__(
        self,
        filename: str,
        lookup: "StructuralIndexLookup",
        self_attrs: dict[str, str] | None = None,
        sibling_provides: dict | None = None,
        local_skip: frozenset[str] | None = None,
    ) -> None:
        SymbolRef, Signature, Reference = _sym_refs()
        self._SymbolRef = SymbolRef
        self._Signature = Signature
        self._Reference = Reference
        self.filename = filename
        self.lookup = lookup
        self.self_attrs = self_attrs or {}
        self.sibling_provides = sibling_provides or {}
        self.local_skip = local_skip or frozenset()
        self.refs: list[Reference] = []
        self._scope_stack: list[dict[str, str]] = [{}]
        self._iter_kinds_stack: list[dict[str, tuple[str, SymbolRef, str]]] = [{}]

    # ---- scope ------------------------------------------------------------

    def _push_scope(self) -> None:
        self._scope_stack.append({})
        self._iter_kinds_stack.append({})

    def _pop_scope(self) -> None:
        self._scope_stack.pop()
        self._iter_kinds_stack.pop()

    def _lookup_type(self, name: str) -> str | None:
        for scope in reversed(self._scope_stack):
            if name in scope:
                return scope[name]
        return None

    def _bind(self, name: str, type_name: str) -> None:
        self._scope_stack[-1][name] = type_name

    def _lookup_iter_kind(self, name: str):
        for scope in reversed(self._iter_kinds_stack):
            if name in scope:
                return scope[name]
        return None

    def _bind_iter_kind(self, name: str, kind: str, ref, context: str) -> None:
        self._iter_kinds_stack[-1][name] = (kind, ref, context)

    # ---- emit -------------------------------------------------------------

    def _emit(self, ref) -> None:
        self.refs.append(ref)

    def _kwargs_of(self, call: "Node") -> frozenset[str]:
        args_list = next((c for c in call.children if c.type == "argument_list"), None)
        if args_list is None:
            return frozenset()
        names: set[str] = set()
        for c in args_list.children:
            if c.type == "keyword_argument":
                ident = next((x for x in c.children if x.type == "identifier"), None)
                if ident is not None:
                    names.add(ident.text.decode("utf-8"))
        return frozenset(names)

    def _emit_annotation_types(self, type_node: "Node | None", context_desc: str) -> None:
        if type_node is None:
            return
        SymbolRef = self._SymbolRef
        Reference = self._Reference
        for name, line in _iter_annotation_class_names(type_node):
            if name in self.local_skip:
                continue
            self._emit(
                Reference(
                    ref=SymbolRef(owner=name, name=None, kind="class"),
                    line=line,
                    context=f"{context_desc}: {name}",
                    usage="call",
                )
            )

    def _emit_call(self, call: "Node", usage: str) -> None:
        """Emit Reference for a call — the ast version's _emit_call logic."""
        SymbolRef = self._SymbolRef
        Reference = self._Reference
        SKIP = _skip_names()
        line = call.start_point[0] + 1
        kwargs = self._kwargs_of(call)
        callee = _callable_of(call)
        if callee is None:
            return

        if callee.type == "attribute":
            idents = [c for c in callee.children if c.type == "identifier"]
            # obj.method()
            if len(idents) == 2 and idents[0].text.decode("utf-8") != "self":
                obj_name = idents[0].text.decode("utf-8")
                attr = idents[1].text.decode("utf-8")
                t = self._lookup_type(obj_name)
                if t and t not in SKIP:
                    self._emit(
                        Reference(
                            ref=SymbolRef(owner=t, name=attr, kind="method"),
                            line=line,
                            context=f"{obj_name}.{attr}()",
                            usage=usage,
                            kwargs=kwargs,
                        )
                    )
                return
            # self._attr.method() — attribute of attribute
            if len(idents) == 0:
                # callee is attribute whose value is another attribute
                # Find the inner attribute + its child pattern
                inner_attr = next((c for c in callee.children if c.type == "attribute"), None)
                if inner_attr is not None:
                    inner_idents = [c for c in inner_attr.children if c.type == "identifier"]
                    if len(inner_idents) == 2 and inner_idents[0].text.decode("utf-8") == "self":
                        attr_name = inner_idents[1].text.decode("utf-8")
                        t = self.self_attrs.get(attr_name)
                        method_ident = next(
                            (c for c in callee.children if c.type == "identifier"), None
                        )
                        if t and t not in SKIP and method_ident is not None:
                            method = method_ident.text.decode("utf-8")
                            self._emit(
                                Reference(
                                    ref=SymbolRef(owner=t, name=method, kind="method"),
                                    line=line,
                                    context=f"self.{attr_name}.{method}()",
                                    usage=usage,
                                    kwargs=kwargs,
                                )
                            )
                return
            # self.method() — grounding handles; do not emit here
            return

        if callee.type == "identifier":
            name = callee.text.decode("utf-8")
            if name not in SKIP and name[:1].isupper():
                self._emit(
                    Reference(
                        ref=SymbolRef(owner=name, name=None, kind="class"),
                        line=line,
                        context=f"{name}(...)",
                        usage=usage,
                        kwargs=kwargs,
                    )
                )

    # ---- main visitor -----------------------------------------------------

    def visit(self, node: "Node") -> None:
        t = node.type
        if t in ("function_definition",):
            self._visit_func(node)
            return
        if t == "decorated_definition":
            inner = _unwrap_decorated(node)
            if inner.type == "function_definition":
                self._visit_func(inner)
                return
            # Class — fall through
            for c in node.children:
                if c.is_named and c.type != "decorator":
                    self.visit(c)
            return
        if t == "assignment":
            self._visit_assign(node)
            return
        if t == "call":
            self._visit_call(node)
            return
        if t == "raise_statement":
            self._visit_raise(node)
            return
        if t == "except_clause":
            self._visit_except(node)
            return
        if t == "for_statement":
            self._visit_for(node, is_async=False)
            return
        if t == "for_in_clause":
            # ``async for`` may appear via for_in_clause inside async with;
            # tree-sitter uses ``for_statement`` for top-level async for too,
            # detected via the ``async`` keyword child.
            self._visit_for(node, is_async=False)
            return
        if t == "await":
            self._visit_await(node)
            return
        if t == "attribute":
            self._visit_attribute(node)
            return
        if t == "import_from_statement":
            self._visit_import_from(node)
            return
        # Default: descend
        for c in node.children:
            self.visit(c)

    # ---- specific visitors -----------------------------------------------

    def _visit_func(self, node: "Node") -> None:
        SKIP = _skip_names()
        self._push_scope()
        params_node = next((c for c in node.children if c.type == "parameters"), None)
        if params_node is not None:
            for p in params_node.children:
                ann_node: Node | None = None
                arg_name: str | None = None
                if p.type == "typed_parameter":
                    ident = next((x for x in p.children if x.type == "identifier"), None)
                    ann_node = next((x for x in p.children if x.type == "type"), None)
                    if ident is not None:
                        arg_name = ident.text.decode("utf-8")
                elif p.type == "typed_default_parameter":
                    ident = next((x for x in p.children if x.type == "identifier"), None)
                    ann_node = next((x for x in p.children if x.type == "type"), None)
                    if ident is not None:
                        arg_name = ident.text.decode("utf-8")
                if ann_node is not None:
                    desc = f"param {arg_name}" if arg_name else "param"
                    self._emit_annotation_types(ann_node, desc)
                    if arg_name is not None:
                        t = extract_type_name(ann_node)
                        if t and t not in SKIP:
                            self._bind(arg_name, t)

        # Return annotation
        ret = _returns_annotation(node)
        if ret is not None:
            self._emit_annotation_types(ret, "return type")

        body = next((c for c in node.children if c.type == "block"), None)
        if body is not None:
            for stmt in body.children:
                self.visit(stmt)
        self._pop_scope()

    def _visit_assign(self, node: "Node") -> None:
        SKIP = _skip_names()
        # Shape: assignment children = [target, (optional: : type), =, value]
        target = next((c for c in node.children if c.is_named), None)
        has_type = any(c.type == "type" for c in node.children)
        if has_type:
            # Annotated assignment — emit annotation types and descend into value
            ann = next((c for c in node.children if c.type == "type"), None)
            desc = target.text.decode("utf-8") if (target and target.type == "identifier") else "var"
            self._emit_annotation_types(ann, f"annotation on {desc}")
            if target is not None and target.type == "identifier":
                t = extract_type_name(ann)
                if t and t not in SKIP:
                    self._bind(target.text.decode("utf-8"), t)
            # Value (if any)
            seen_eq = False
            value = None
            for c in node.children:
                if not c.is_named and c.type == "=":
                    seen_eq = True
                    continue
                if seen_eq and c.is_named:
                    value = c
                    break
            if value is not None:
                self.visit(value)
            return

        # Plain assignment
        seen_eq = False
        value: Node | None = None
        for c in node.children:
            if not c.is_named and c.type == "=":
                seen_eq = True
                continue
            if seen_eq and c.is_named:
                value = c
                break
        if target is not None and target.type == "identifier" and value is not None:
            var_name = target.text.decode("utf-8")
            t = self._infer_rhs_type(value)
            if t:
                self._bind(var_name, t)
            kind_info = self._infer_rhs_iter_kind(value)
            if kind_info is not None:
                kind, ref, context = kind_info
                self._bind_iter_kind(var_name, kind, ref, context)
        # Descend into value for nested calls/etc.
        if value is not None:
            self.visit(value)

    def _visit_call(self, node: "Node") -> None:
        callee = _callable_of(node)
        # isinstance/issubclass/cast special case
        if callee is not None and callee.type == "identifier":
            fname = callee.text.decode("utf-8")
            args_list = next((c for c in node.children if c.type == "argument_list"), None)
            if args_list is not None:
                named_args = [c for c in args_list.children if c.is_named and c.type != "keyword_argument"]
                if fname in ("isinstance", "issubclass") and len(named_args) >= 2:
                    self._emit_annotation_types(named_args[1], f"{fname} type")
                elif fname == "cast" and len(named_args) >= 1:
                    self._emit_annotation_types(named_args[0], "cast type")
        self._emit_call(node, usage="call")
        # Descend into args (not callee — already handled)
        args_list = next((c for c in node.children if c.type == "argument_list"), None)
        if args_list is not None:
            for c in args_list.children:
                if c.is_named:
                    self.visit(c)

    def _visit_raise(self, node: "Node") -> None:
        SymbolRef = self._SymbolRef
        Reference = self._Reference
        SKIP = _skip_names()
        # Body is the first named child after the ``raise`` keyword
        body = next((c for c in node.children if c.is_named), None)
        if body is not None and body.type == "identifier":
            name = body.text.decode("utf-8")
            if name[:1].isupper() and name not in SKIP:
                self._emit(
                    Reference(
                        ref=SymbolRef(owner=name, name=None, kind="class"),
                        line=node.start_point[0] + 1,
                        context=f"raise {name}",
                        usage="call",
                    )
                )
        # Descend into children (e.g. for raise Foo(bar))
        for c in node.children:
            if c.is_named:
                self.visit(c)

    def _visit_except(self, node: "Node") -> None:
        # except_clause children include ``except`` keyword and the type expr
        for c in node.children:
            if c.is_named:
                if c.type == "identifier" or c.type == "attribute" or c.type == "tuple":
                    self._emit_annotation_types(c, "except")
                    continue
                self.visit(c)

    def _visit_for(self, node: "Node", is_async: bool) -> None:
        # Detect async: tree-sitter puts an ``async`` keyword child on async for
        async_kw = any(c.type == "async" for c in node.children)
        is_async = is_async or async_kw
        # Children: for identifier in iterable: block
        # Find `in` keyword then the iterable node after it
        saw_in = False
        target_node: Node | None = None
        iter_node: Node | None = None
        body_node: Node | None = None
        for c in node.children:
            if not c.is_named and c.type == "in":
                saw_in = True
                continue
            if c.is_named and target_node is None and not saw_in:
                target_node = c
                continue
            if saw_in and iter_node is None and c.is_named and c.type != "block":
                iter_node = c
                continue
            if c.type == "block":
                body_node = c

        usage = "async_iter" if is_async else "iter"
        if iter_node is not None:
            if iter_node.type == "call":
                self._emit_call(iter_node, usage=usage)
                args_list = next((c for c in iter_node.children if c.type == "argument_list"), None)
                if args_list is not None:
                    for c in args_list.children:
                        if c.is_named:
                            self.visit(c)
            elif iter_node.type == "identifier":
                self._propagate_var_usage(
                    iter_node.text.decode("utf-8"),
                    used_as=usage,
                    line=iter_node.start_point[0] + 1,
                )
            else:
                self.visit(iter_node)
        if target_node is not None:
            self.visit(target_node)
        if body_node is not None:
            for stmt in body_node.children:
                self.visit(stmt)

    def _visit_await(self, node: "Node") -> None:
        # Body is the awaited expression
        body = next((c for c in node.children if c.is_named), None)
        if body is None:
            return
        if body.type == "call":
            self._emit_call(body, usage="await")
            args_list = next((c for c in body.children if c.type == "argument_list"), None)
            if args_list is not None:
                for c in args_list.children:
                    if c.is_named:
                        self.visit(c)
        elif body.type == "identifier":
            self._propagate_var_usage(
                body.text.decode("utf-8"),
                used_as="await",
                line=body.start_point[0] + 1,
            )
        else:
            self.visit(body)

    def _propagate_var_usage(self, var_name: str, used_as: str, line: int) -> None:
        Reference = self._Reference
        info = self._lookup_iter_kind(var_name)
        if info is None:
            return
        _kind, ref, context = info
        self._emit(
            Reference(
                ref=ref,
                line=line,
                context=f"{context} → {var_name}",
                usage=used_as,
            )
        )

    def _visit_attribute(self, node: "Node") -> None:
        SymbolRef = self._SymbolRef
        Reference = self._Reference
        SKIP = _skip_names()
        idents = [c for c in node.children if c.type == "identifier"]
        if len(idents) == 2:
            obj = idents[0].text.decode("utf-8")
            attr = idents[1].text.decode("utf-8")
            if obj != "self":
                t = self._lookup_type(obj)
                if t and t not in SKIP:
                    self._emit(
                        Reference(
                            ref=SymbolRef(owner=t, name=attr, kind="field"),
                            line=node.start_point[0] + 1,
                            context=f"{obj}.{attr}",
                            usage="field_access",
                        )
                    )
        # Descend into children (e.g. for a.b.c which has nested attribute)
        for c in node.children:
            if c.is_named and c.type == "attribute":
                self.visit(c)

    def _visit_import_from(self, node: "Node") -> None:
        SymbolRef = self._SymbolRef
        Reference = self._Reference
        STDLIB = _stdlib_packages()
        # Find module (dotted_name or relative_import) and imported names
        module_node = next(
            (c for c in node.children if c.type in ("dotted_name", "relative_import")),
            None,
        )
        if module_node is None:
            return
        module_text = module_node.text.decode("utf-8")
        if not module_text:
            return
        top = module_text.split(".")[0]
        if top in STDLIB:
            return
        # Names come after the ``import`` keyword
        saw_import = False
        imported_identifiers: list[str] = []
        for c in node.children:
            if not c.is_named and c.type == "import":
                saw_import = True
                continue
            if not saw_import:
                continue
            if c.type == "dotted_name":
                # Simple name (from x import foo)
                imported_identifiers.append(c.text.decode("utf-8"))
            elif c.type == "aliased_import":
                # Original name is the first dotted_name child
                orig = next((x for x in c.children if x.type == "dotted_name"), None)
                if orig is not None:
                    imported_identifiers.append(orig.text.decode("utf-8"))
            elif c.type == "wildcard_import":
                return
        for name in imported_identifiers:
            if name == "*":
                continue
            self._emit(
                Reference(
                    ref=SymbolRef(owner=module_text, name=name, kind="import_name"),
                    line=node.start_point[0] + 1,
                    context=f"from {module_text} import {name}",
                    usage="import",
                )
            )

    # ---- RHS type inference ----------------------------------------------

    def _infer_rhs_type(self, value: "Node") -> str | None:
        SKIP = _skip_names()
        locator_fn = _locator_return_type_fn()
        if value.type != "call":
            return None
        callee = _callable_of(value)
        if callee is None:
            return None
        if callee.type == "identifier":
            fid = callee.text.decode("utf-8")
            if fid[:1].isupper() and fid not in SKIP:
                return fid
            rt = locator_fn(fid, self.lookup)
            if rt:
                return rt
        elif callee.type == "attribute":
            attr_name = _rightmost_attribute_name(callee)
            if attr_name and attr_name[:1].isupper() and attr_name not in SKIP:
                return attr_name
        return None

    def _infer_rhs_iter_kind(self, value: "Node"):
        looks_like_gen, is_async_iter, is_awaitable = _classifiers()
        if value.type != "call":
            return None
        resolved = self._resolve_call_target(value)
        if resolved is None:
            return None
        ref, return_type, context = resolved
        if return_type is None:
            return None
        if is_async_iter(return_type):
            return ("async_iter", ref, context)
        if is_awaitable(return_type):
            return ("awaitable", ref, context)
        if looks_like_gen(return_type):
            return ("sync_iter", ref, context)
        return None

    def _resolve_call_target(self, call: "Node"):
        SymbolRef = self._SymbolRef
        SKIP = _skip_names()
        callee = _callable_of(call)
        if callee is None:
            return None

        # typed_obj.method(...)
        if callee.type == "attribute":
            idents = [c for c in callee.children if c.type == "identifier"]
            if len(idents) == 2:
                obj_name = idents[0].text.decode("utf-8")
                attr = idents[1].text.decode("utf-8")
                if obj_name == "self":
                    return None  # handled via self._attr below
                obj_type = self._lookup_type(obj_name)
                if not obj_type or obj_type in SKIP:
                    return None
                ref = SymbolRef(owner=obj_type, name=attr, kind="method")
                return (ref, self._lookup_return_type(ref), f"{obj_name}.{attr}()")
            # self._attr.method(...)
            if len(idents) == 0:
                inner_attr = next((c for c in callee.children if c.type == "attribute"), None)
                method_ident = next((c for c in callee.children if c.type == "identifier"), None)
                if inner_attr is not None and method_ident is not None:
                    inner_idents = [c for c in inner_attr.children if c.type == "identifier"]
                    if len(inner_idents) == 2 and inner_idents[0].text.decode("utf-8") == "self":
                        attr_name = inner_idents[1].text.decode("utf-8")
                        method = method_ident.text.decode("utf-8")
                        attr_type = self.self_attrs.get(attr_name)
                        if not attr_type or attr_type in SKIP:
                            return None
                        ref = SymbolRef(owner=attr_type, name=method, kind="method")
                        return (
                            ref,
                            self._lookup_return_type(ref),
                            f"self.{attr_name}.{method}()",
                        )
            return None

        # top_level_func(...)
        if callee.type == "identifier":
            fid = callee.text.decode("utf-8")
            if fid in SKIP or fid[:1].isupper():
                return None
            ref = SymbolRef(owner=None, name=fid, kind="function")
            sig = self.sibling_provides.get(ref)
            if sig and sig.return_type:
                return (ref, sig.return_type, f"{fid}()")
            funcs = self.lookup.find_function(fid)
            if funcs:
                return (ref, funcs[0].return_type, f"{fid}()")
            return None
        return None

    def _lookup_return_type(self, ref) -> str | None:
        sig = self.sibling_provides.get(ref)
        if sig is None:
            SymbolRef = self._SymbolRef
            for k in ("method", "field", "function"):
                alt = SymbolRef(owner=ref.owner, name=ref.name, kind=k)
                sig = self.sibling_provides.get(alt)
                if sig is not None:
                    break
        if sig and sig.return_type:
            return sig.return_type
        if ref.owner and ref.name:
            for cls in self.lookup.classes.get(ref.owner, []):
                if ref.name in cls.methods:
                    return cls.methods[ref.name].return_type
        return None


def extract_references(
    content: str,
    filename: str,
    lookup: "StructuralIndexLookup",
    self_attrs: dict[str, str] | None = None,
    sibling_provides: dict | None = None,
) -> "list[Reference]":
    tree = parse_python(content)
    if tree is None:
        return []
    local_skip = frozenset(_find_module_typevars(tree.root_node))
    collector = _TsReferenceCollector(
        filename,
        lookup,
        self_attrs=self_attrs,
        sibling_provides=sibling_provides,
        local_skip=local_skip,
    )
    collector.visit(tree.root_node)
    return collector.refs


# ---------------------------------------------------------------------------
# load_target_self_attrs — disk-read + __init__ self-attr extraction
# ---------------------------------------------------------------------------


def load_target_self_attrs(
    filename: str,
    source_dir: str,
    lookup: "StructuralIndexLookup",
) -> dict[str, str]:
    if not source_dir:
        return {}
    disk = Path(source_dir) / filename
    if not disk.is_file():
        return {}
    try:
        source = disk.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    tree = parse_python(source)
    if tree is None:
        return {}
    target = _target_class_for_file(filename, lookup)
    if not target:
        return {}
    known = set(lookup._all_class_names) if hasattr(lookup, "_all_class_names") else None
    for cls in iter_all_classes(tree.root_node):
        name_node = next((c for c in cls.children if c.type == "identifier"), None)
        if name_node is not None and name_node.text.decode("utf-8") == target:
            return extract_init_self_attrs(cls, known_classes=known)
    return {}
