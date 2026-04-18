# fitz_forge/planning/artifact/closure.py
"""Plan-level closure family of invariants on the artifact set.

Five invariants, one architecture:

    1. EXISTENCE  — every cross-file symbol an artifact references must be
                    satisfied by the existing codebase or by a sibling
                    artifact in the same plan.
    2. USAGE      — every reference must be used consistent with the
                    callee's signature (async for only on async iterators,
                    await only on coroutines, plain for on sync iters).
    3. KWARGS     — keyword arguments must match the callee's parameter
                    names.
    4. IMPORTS    — `from pkg.mod import X` → X must resolve.
    5. FIELDS     — `obj.field` on a typed local → the field must exist on
                    that type.

Per-artifact validation in `validate.py` cannot enforce any of these
because they're properties of the *set*. `check_closure` runs all five.

Reuses `grounding.inference` for type tracking primitives (return type
inference, self._attr extraction, field parsing), keeping codebase
knowledge in one place.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import TYPE_CHECKING

from fitz_forge.planning.validation.grounding import (
    _SKIP_NAMES as _GROUNDING_SKIP_NAMES,
)
from fitz_forge.planning.validation.grounding import (
    StructuralIndexLookup,
)
from fitz_forge.planning.validation.grounding.inference import (
    _callable_of,
    _class_body,
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
from fitz_forge.planning.validation.grounding.parser import parse_python

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolRef:
    """A cross-file symbol reference or definition.

    Forms:
        SymbolRef("FitzService", "query_stream", "method")    — method on a class
        SymbolRef("FitzService", None, "class")               — class use
        SymbolRef(None, "get_service", "function")            — top-level function
        SymbolRef("fitz_sage.schemas", "ChatRequest", "import_name") — import
        SymbolRef("ChatRequest", "message", "field")          — field access
    """

    owner: str | None
    name: str | None
    kind: str = "method"  # "method" | "class" | "function" | "field" | "import_name"

    def pretty(self) -> str:
        if self.owner and self.name:
            sep = "/" if self.kind == "import_name" else "."
            return f"{self.owner}{sep}{self.name}"
        if self.owner:
            return self.owner
        return self.name or "?"


@dataclass
class Signature:
    """A method/function signature extracted from an artifact or codebase."""

    params: list[str] = dc_field(default_factory=list)  # excludes self
    return_type: str | None = None
    is_async: bool = False
    is_generator: bool = False  # body contains yield
    has_var_keywords: bool = False  # signature has **kwargs — permissive


@dataclass
class Reference:
    """A concrete use site of a symbol with context for usage/kwargs checks."""

    ref: SymbolRef
    line: int = 0
    context: str = ""
    usage: str = "call"  # "call" | "async_iter" | "iter" | "await" | "field_access" | "import"
    kwargs: frozenset[str] = frozenset()


@dataclass
class ClosureViolation:
    """A cross-artifact invariant violation."""

    artifact: str
    ref: SymbolRef
    line: int
    context: str = ""
    kind: str = "missing"
    # "missing" | "usage" | "kwargs" | "import" | "field"
    detail: str = ""

    def pretty(self) -> str:
        base = f"{self.artifact}:{self.line} — {self.ref.pretty()}"
        if self.kind == "missing":
            return f"{base} ({self.context})"
        return f"{base} [{self.kind}: {self.detail}]"


# ---------------------------------------------------------------------------
# Skip names — grounding's canonical list plus closure-specific additions
# ---------------------------------------------------------------------------

_SKIP_NAMES = _GROUNDING_SKIP_NAMES | frozenset(
    {
        "AsyncIterator",
        "AsyncGenerator",
        "Awaitable",
        "Coroutine",
        "uuid",
        "asyncio",
        "functools",
        "itertools",
    }
)

# Stdlib / 3rd-party module prefixes — imports from these are never closure-checked.
_STDLIB_PACKAGES = frozenset(
    {
        "typing",
        "collections",
        "abc",
        "functools",
        "itertools",
        "asyncio",
        "dataclasses",
        "contextlib",
        "pathlib",
        "json",
        "re",
        "os",
        "sys",
        "time",
        "uuid",
        "logging",
        "enum",
        "datetime",
        "decimal",
        "math",
        "random",
        "hashlib",
        "base64",
        "struct",
        "pickle",
        "copy",
        "types",
        "inspect",
        "warnings",
        "traceback",
        "io",
        "tempfile",
        "shutil",
        "subprocess",
        "threading",
        "concurrent",
        "queue",
        "weakref",
        "pydantic",
        "fastapi",
        "starlette",
        "httpx",
        "requests",
        "aiohttp",
        "uvicorn",
        "sqlalchemy",
        "pytest",
        "typer",
        "click",
        "rich",
        "numpy",
        "pandas",
        "anthropic",
        "openai",
        "cohere",
        "ollama",
        "mistralai",
        "google",
    }
)


# ---------------------------------------------------------------------------
# Enum standard members + protocol widening helpers
# ---------------------------------------------------------------------------


_ENUM_STANDARD_ATTRS = frozenset(
    {"value", "name", "_value_", "_name_", "_ignore_", "_order_", "_missing_"}
)
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})


def _is_enum_class(class_name: str, lookup: StructuralIndexLookup) -> bool:
    """True if `class_name`'s MRO includes an Enum base.

    Enum subclasses get `.value` and `.name` (plus a few other dunders)
    for free from `enum.Enum`. Those aren't in the class's own field
    set, so `class_has_field` says missing. This walks up to check.
    """
    seen: set[str] = set()
    stack = [class_name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in _ENUM_BASES:
            return True
        for cls in lookup.classes.get(current, []):
            for base in cls.bases:
                base_clean = base.split("[")[0].strip()
                if base_clean and base_clean not in seen:
                    stack.append(base_clean)
    return False


def _owner_is_protocol(class_name: str, lookup: StructuralIndexLookup) -> bool:
    """True if `class_name` declares `Protocol` as a base.

    Protocol classes are structural: any object that has the right
    methods satisfies them, regardless of declared type. So when
    `obj: MyProtocol` is called with `obj.foo()` and `foo` isn't on
    `MyProtocol`, the runtime instance may still have it.
    """
    for cls in lookup.classes.get(class_name, []):
        for base in cls.bases:
            if base.split("[")[0].strip() == "Protocol":
                return True
    return False


def _method_exists_anywhere(method_name: str, lookup: StructuralIndexLookup) -> bool:
    """True if any class in the codebase defines a method named `method_name`."""
    for cls_list in lookup.classes.values():
        for cls in cls_list:
            if method_name in cls.methods:
                return True
    return False


# ---------------------------------------------------------------------------
# Annotation walking — yields every class-shaped Name in a type position
# ---------------------------------------------------------------------------


def _iter_annotation_class_names(type_node: "Node | None") -> "Iterator[tuple[str, int]]":
    """Yield (class_name, line) for class-shaped identifiers in an annotation.

    Walks nested generics (List[Foo], Dict[str, Bar], Foo | None, Optional[X])
    and emits one tuple per capitalized identifier that isn't in _SKIP_NAMES.

    Single-letter uppercase names (T, K, V, P, R, ...) are skipped: they're
    conventionally TypeVars, and fabricated schema classes are never that
    short. Longer TypeVar names are handled by per-artifact detection in
    `_find_module_typevars`.
    """
    if type_node is None:
        return
    stack: list[Node] = [type_node]
    while stack:
        n = stack.pop()
        if n.type == "identifier":
            name = n.text.decode("utf-8")
            if name[:1].isupper() and name not in _SKIP_NAMES and len(name) != 1:
                yield name, n.start_point[0] + 1
        stack.extend(n.children)


def _find_module_typevars(root: "Node") -> set[str]:
    """Collect names assigned to TypeVar/ParamSpec/TypeVarTuple calls.

    Catches `T = TypeVar("T")`, `P = ParamSpec("P")`, `Ts = TypeVarTuple("Ts")`
    at any scope. These names act as type-level placeholders and must not be
    checked as if they were concrete class references.
    """
    out: set[str] = set()
    stack: list[Node] = [root]
    while stack:
        n = stack.pop()
        if n.type == "assignment":
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
# Locator-style return type inference for closure-local scope
# ---------------------------------------------------------------------------

_LOCATOR_PREFIXES = ("get_", "make_", "create_", "build_", "new_")


def _locator_return_type(func_name: str, lookup: StructuralIndexLookup) -> str | None:
    """If func_name looks like a locator and the index knows its return type, return it.

    Used when an artifact does `var = get_service()` to bind `var: <return_type>`.
    """
    if not any(func_name.startswith(p) for p in _LOCATOR_PREFIXES):
        return None
    funcs = lookup.find_function(func_name)
    if not funcs:
        return None
    ret = funcs[0].return_type
    if not ret:
        return None
    m = re.search(r"[A-Z][A-Za-z_0-9]*", ret)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# self._attr loading — reuse grounding.inference
# ---------------------------------------------------------------------------


def load_target_self_attrs(
    filename: str,
    source_dir: str,
    lookup: StructuralIndexLookup,
) -> dict[str, str]:
    """Locate the file on disk, find the primary class, parse its __init__."""
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


def extract_self_attrs_from_content(
    content: str,
    lookup: StructuralIndexLookup,
) -> dict[str, str]:
    """Extract self-attr types from every class defined in the artifact itself.

    Parses `content` as Python, walks every class, reads its `__init__`
    bodies to find `self._x = ...` bindings. Used for in-memory artifacts
    where the target class is defined by the artifact (no disk source to
    load from). Returns a merged dict across all classes in the file.
    """
    if not content:
        return {}
    tree = parse_python(content)
    if tree is None:
        return {}
    known = set(lookup._all_class_names) if hasattr(lookup, "_all_class_names") else None
    out: dict[str, str] = {}
    for cls in iter_all_classes(tree.root_node):
        out.update(extract_init_self_attrs(cls, known_classes=known))
    return out


def _target_class_for_file(filename: str, lookup: StructuralIndexLookup) -> str | None:
    """Find the primary class owning `filename` (most methods wins)."""
    candidates: list[tuple[str, int]] = []
    for cls_list in lookup.classes.values():
        for cls in cls_list:
            if cls.file == filename or filename.endswith(cls.file):
                candidates.append((cls.name, len(cls.methods)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


# ---------------------------------------------------------------------------
# Reference collector — walks an artifact tree-sitter tree, emits Reference objects
# ---------------------------------------------------------------------------


class _ReferenceCollector:
    """Walks one artifact and collects cross-file Reference objects.

    Type tracking scope:
      - function param annotations         (per-function scope)
      - `var = ClassName(...)`             (local scope)
      - `var = locator_func()`              (locators → return type via index)
      - self._attr (provided by caller)    (target class __init__)
    """

    def __init__(
        self,
        filename: str,
        lookup: StructuralIndexLookup,
        self_attrs: dict[str, str] | None = None,
        sibling_provides: dict[SymbolRef, Signature | None] | None = None,
        local_skip: frozenset[str] | None = None,
    ) -> None:
        self.filename = filename
        self.lookup = lookup
        self.self_attrs = self_attrs or {}
        self.sibling_provides = sibling_provides or {}
        self.local_skip = local_skip or frozenset()
        self.refs: list[Reference] = []
        self._scope_stack: list[dict[str, str]] = [{}]
        # Iterator/awaitable kind tracked per variable for async-for/await
        # propagation. Maps var name → (kind, originating_ref, context) where
        # kind ∈ {"sync_iter", "async_iter", "awaitable"}. Cleared on function
        # scope pop alongside type bindings.
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

    def _lookup_iter_kind(self, name: str) -> tuple[str, SymbolRef, str] | None:
        for scope in reversed(self._iter_kinds_stack):
            if name in scope:
                return scope[name]
        return None

    def _bind_iter_kind(self, name: str, kind: str, ref: SymbolRef, context: str) -> None:
        self._iter_kinds_stack[-1][name] = (kind, ref, context)

    # ---- emit -------------------------------------------------------------

    def _emit(self, ref: Reference) -> None:
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
        """Emit Reference for a call node."""
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
                if t and t not in _SKIP_NAMES:
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
            # self._attr.method() — attribute of attribute. Outer attribute
            # has children [attribute("self._attr"), identifier("method")],
            # so idents == 1 (the method-name) and inner_attrs == 1.
            inner_attrs = [c for c in callee.children if c.type == "attribute"]
            if len(idents) == 1 and len(inner_attrs) == 1:
                inner_attr = inner_attrs[0]
                method_ident = idents[0]
                inner_idents = [c for c in inner_attr.children if c.type == "identifier"]
                if len(inner_idents) == 2 and inner_idents[0].text.decode("utf-8") == "self":
                    attr_name = inner_idents[1].text.decode("utf-8")
                    t = self.self_attrs.get(attr_name)
                    if t and t not in _SKIP_NAMES:
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
            if name not in _SKIP_NAMES and name[:1].isupper():
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
        if t == "function_definition":
            self._visit_func(node)
            return
        if t == "decorated_definition":
            inner = _unwrap_decorated(node)
            if inner.type == "function_definition":
                self._visit_func(inner)
                return
            # Class — descend into body
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
                        if t and t not in _SKIP_NAMES:
                            self._bind(arg_name, t)

        # Return annotation
        ret = _returns_annotation_node(node)
        if ret is not None:
            self._emit_annotation_types(ret, "return type")

        body = next((c for c in node.children if c.type == "block"), None)
        if body is not None:
            for stmt in body.children:
                self.visit(stmt)
        self._pop_scope()

    def _visit_assign(self, node: "Node") -> None:
        target = next((c for c in node.children if c.is_named), None)
        has_type = any(c.type == "type" for c in node.children)
        if has_type:
            ann = next((c for c in node.children if c.type == "type"), None)
            desc = (
                target.text.decode("utf-8")
                if (target and target.type == "identifier")
                else "var"
            )
            self._emit_annotation_types(ann, f"annotation on {desc}")
            if target is not None and target.type == "identifier":
                t = extract_type_name(ann)
                if t and t not in _SKIP_NAMES:
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
        if value is not None:
            self.visit(value)

    def _visit_call(self, node: "Node") -> None:
        callee = _callable_of(node)
        # isinstance/issubclass/cast special case
        if callee is not None and callee.type == "identifier":
            fname = callee.text.decode("utf-8")
            args_list = next((c for c in node.children if c.type == "argument_list"), None)
            if args_list is not None:
                named_args = [
                    c for c in args_list.children
                    if c.is_named and c.type != "keyword_argument"
                ]
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
        body = next((c for c in node.children if c.is_named), None)
        if body is not None and body.type == "identifier":
            name = body.text.decode("utf-8")
            if name[:1].isupper() and name not in _SKIP_NAMES:
                self._emit(
                    Reference(
                        ref=SymbolRef(owner=name, name=None, kind="class"),
                        line=node.start_point[0] + 1,
                        context=f"raise {name}",
                        usage="call",
                    )
                )
        for c in node.children:
            if c.is_named:
                self.visit(c)

    def _visit_except(self, node: "Node") -> None:
        for c in node.children:
            if c.is_named:
                if c.type in ("identifier", "attribute", "tuple"):
                    self._emit_annotation_types(c, "except")
                    continue
                self.visit(c)

    def _visit_for(self, node: "Node", is_async: bool) -> None:
        async_kw = any(c.type == "async" for c in node.children)
        is_async = is_async or async_kw
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
        idents = [c for c in node.children if c.type == "identifier"]
        if len(idents) == 2:
            obj = idents[0].text.decode("utf-8")
            attr = idents[1].text.decode("utf-8")
            if obj != "self":
                t = self._lookup_type(obj)
                if t and t not in _SKIP_NAMES:
                    self._emit(
                        Reference(
                            ref=SymbolRef(owner=t, name=attr, kind="field"),
                            line=node.start_point[0] + 1,
                            context=f"{obj}.{attr}",
                            usage="field_access",
                        )
                    )
        for c in node.children:
            if c.is_named and c.type == "attribute":
                self.visit(c)

    def _visit_import_from(self, node: "Node") -> None:
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
        if top in _STDLIB_PACKAGES:
            return
        saw_import = False
        imported_identifiers: list[str] = []
        for c in node.children:
            if not c.is_named and c.type == "import":
                saw_import = True
                continue
            if not saw_import:
                continue
            if c.type == "dotted_name":
                imported_identifiers.append(c.text.decode("utf-8"))
            elif c.type == "aliased_import":
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
        if value.type != "call":
            return None
        callee = _callable_of(value)
        if callee is None:
            return None
        if callee.type == "identifier":
            fid = callee.text.decode("utf-8")
            if fid[:1].isupper() and fid not in _SKIP_NAMES:
                return fid
            rt = _locator_return_type(fid, self.lookup)
            if rt:
                return rt
        elif callee.type == "attribute":
            attr_name = _rightmost_attribute_name(callee)
            if attr_name and attr_name[:1].isupper() and attr_name not in _SKIP_NAMES:
                return attr_name
        return None

    def _infer_rhs_iter_kind(
        self, value: "Node"
    ) -> tuple[str, SymbolRef, str] | None:
        if value.type != "call":
            return None
        resolved = self._resolve_call_target(value)
        if resolved is None:
            return None
        ref, return_type, context = resolved
        if return_type is None:
            return None
        if _is_async_iter_type(return_type):
            return ("async_iter", ref, context)
        if _is_awaitable_type(return_type):
            return ("awaitable", ref, context)
        if _looks_like_generator(return_type):
            return ("sync_iter", ref, context)
        return None

    def _resolve_call_target(
        self, call: "Node"
    ) -> tuple[SymbolRef, str | None, str] | None:
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
                if not obj_type or obj_type in _SKIP_NAMES:
                    return None
                ref = SymbolRef(owner=obj_type, name=attr, kind="method")
                return (ref, self._lookup_return_type(ref), f"{obj_name}.{attr}()")
            # self._attr.method(...) — outer attribute is
            # [attribute("self._attr"), identifier("method")].
            inner_attrs = [c for c in callee.children if c.type == "attribute"]
            if len(idents) == 1 and len(inner_attrs) == 1:
                inner_attr = inner_attrs[0]
                method_ident = idents[0]
                inner_idents = [c for c in inner_attr.children if c.type == "identifier"]
                if len(inner_idents) == 2 and inner_idents[0].text.decode("utf-8") == "self":
                    attr_name = inner_idents[1].text.decode("utf-8")
                    method = method_ident.text.decode("utf-8")
                    attr_type = self.self_attrs.get(attr_name)
                    if not attr_type or attr_type in _SKIP_NAMES:
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
            if fid in _SKIP_NAMES or fid[:1].isupper():
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

    def _lookup_return_type(self, ref: SymbolRef) -> str | None:
        sig = self.sibling_provides.get(ref)
        if sig is None:
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


def _returns_annotation_node(func_def: "Node") -> "Node | None":
    """Return the return-type annotation node of a function_definition, or None."""
    saw_params = False
    for c in func_def.children:
        if c.type == "parameters":
            saw_params = True
            continue
        if saw_params and c.type == "type":
            return c
    return None


def _has_yield(func_def: "Node") -> bool:
    """True if the function body contains any yield node."""
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


def extract_references(
    content: str,
    filename: str,
    lookup: StructuralIndexLookup,
    self_attrs: dict[str, str] | None = None,
    sibling_provides: dict[SymbolRef, Signature | None] | None = None,
) -> list[Reference]:
    """Walk an artifact and return its cross-file references.

    `sibling_provides` enables the collector to resolve cross-file method
    return types for variable-binding propagation (so `var = obj.method()`
    followed by `async for x in var` can be checked).
    """
    tree = parse_python(content)
    if tree is None:
        return []
    local_skip = frozenset(_find_module_typevars(tree.root_node))
    collector = _ReferenceCollector(
        filename,
        lookup,
        self_attrs=self_attrs,
        sibling_provides=sibling_provides,
        local_skip=local_skip,
    )
    collector.visit(tree.root_node)
    return collector.refs


# ---------------------------------------------------------------------------
# Provides: what an artifact contributes (symbols + signatures)
# ---------------------------------------------------------------------------


def _sig_from_funcdef(func_def: "Node") -> Signature:
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

    ret: str | None = None
    ret_node = _returns_annotation_node(func_def)
    if ret_node is not None:
        ret = unparse_annotation(ret_node)
    return Signature(
        params=params,
        return_type=ret,
        is_async=_function_is_async(func_def),
        is_generator=_has_yield(func_def),
        has_var_keywords=has_var_kw,
    )


def extract_provides(
    content: str,
    filename: str,
    lookup: StructuralIndexLookup,
    is_surgical: bool | None = None,
) -> dict[SymbolRef, Signature | None]:
    """Extract symbols an artifact defines, keyed by SymbolRef with signatures.

    Returns a dict so usage checks can look up signatures. Class refs map to
    None; method/function refs map to their Signature.

    `is_surgical`: when the caller knows the artifact's generation strategy
    (SurgicalRewriteStrategy vs NewCodeStrategy), it should pass the explicit
    boolean. This is the canonical source of truth (see B15) — the artifact
    pipeline's strategy classification is what determines whether top-level
    `def` items belong to a target class. When `None`, fall back to a
    whitespace-based heuristic that misclassifies dedented surgical artifacts
    as new code; the heuristic exists so test code and one-off callers without
    strategy info still work.
    """
    out: dict[SymbolRef, Signature | None] = {}
    tree = parse_python(content)
    if tree is None:
        return out
    root = tree.root_node

    if is_surgical is None:
        # Fallback heuristic — prefer the explicit caller-provided value when
        # available. This whitespace check misclassifies surgical artifacts
        # emitted at column 0 (the dominant real-world shape, see B15).
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
# Resolution & typing helpers
# ---------------------------------------------------------------------------


def _ref_in_codebase(ref: SymbolRef, lookup: StructuralIndexLookup) -> bool:
    """Check whether the reference is satisfied by the existing codebase."""
    if ref.kind == "import_name":
        return _import_in_codebase(ref, lookup)
    if ref.owner and ref.name:
        if ref.kind == "field":
            if lookup.class_has_field(ref.owner, ref.name):
                return True
            # Enum subclasses inherit `.value` / `.name` / etc from enum.Enum.
            # The index doesn't track those (they come from stdlib), so accept
            # them when the owner walks to an Enum base.
            return ref.name in _ENUM_STANDARD_ATTRS and _is_enum_class(ref.owner, lookup)
        # For methods (MRO-aware walk)
        if lookup.class_has_method(ref.owner, ref.name):
            return True
        # Protocol widening: `obj: SomeProtocol` calling a method that
        # isn't on SomeProtocol but exists on some concrete class in the
        # codebase is legitimate duck-typing. Accept when the owner is a
        # Protocol and the method exists anywhere in the codebase.
        return _owner_is_protocol(ref.owner, lookup) and _method_exists_anywhere(ref.name, lookup)
    if ref.owner:
        return lookup.class_exists(ref.owner)
    if ref.name:
        return lookup.function_exists(ref.name)
    return True


def _import_in_codebase(ref: SymbolRef, lookup: StructuralIndexLookup) -> bool:
    if not ref.name:
        return True
    return lookup.class_exists(ref.name) or lookup.function_exists(ref.name)


def _signature_of(
    ref: SymbolRef,
    lookup: StructuralIndexLookup,
    sibling_provides: dict[SymbolRef, Signature | None],
) -> Signature | None:
    """Look up the signature of a resolved reference (siblings first, then codebase)."""
    if ref in sibling_provides:
        return sibling_provides[ref]
    for k in ("method", "field", "function", "class"):
        alt = SymbolRef(owner=ref.owner, name=ref.name, kind=k)
        if alt in sibling_provides:
            return sibling_provides[alt]

    if ref.owner and ref.name:
        for cls in lookup.classes.get(ref.owner, []):
            if ref.name in cls.methods:
                m = cls.methods[ref.name]
                return Signature(
                    params=[],  # codebase methods don't expose params in the index
                    return_type=m.return_type,
                    is_async=False,
                    is_generator=_looks_like_generator(m.return_type),
                    has_var_keywords=True,  # permissive for codebase methods
                )
    if ref.name and not ref.owner:
        funcs = lookup.find_function(ref.name)
        if funcs:
            f = funcs[0]
            return Signature(
                params=f.params,
                return_type=f.return_type,
                is_async=False,
                is_generator=_looks_like_generator(f.return_type),
                has_var_keywords=True,
            )
    return None


def _looks_like_generator(return_type: str | None) -> bool:
    if not return_type:
        return False
    return any(x in return_type for x in ("Iterator", "Generator", "Iterable")) and not any(
        x in return_type for x in ("AsyncIterator", "AsyncGenerator", "AsyncIterable")
    )


def _is_async_iter_type(return_type: str | None) -> bool:
    if not return_type:
        return False
    return any(x in return_type for x in ("AsyncIterator", "AsyncGenerator", "AsyncIterable"))


def _is_awaitable_type(return_type: str | None) -> bool:
    if not return_type:
        return False
    return any(x in return_type for x in ("Awaitable", "Coroutine", "Future", "Task"))


# ---------------------------------------------------------------------------
# Individual checks (existence, usage, kwargs)
# ---------------------------------------------------------------------------


def _close_method_matches(
    target: str, candidates: "Iterator[str] | list[str] | set[str]", limit: int = 3
) -> list[str]:
    """Top-`limit` closest method-name matches by edit distance.

    Used to enrich missing-method violations with concrete suggestions
    ("did you mean X?"). Pure name comparison — no semantics, no
    type-system assumptions, so it works for any language.
    """
    if not target:
        return []
    cands = [c for c in candidates if c and c != target]
    if not cands:
        return []
    scored: list[tuple[int, str]] = []
    for c in cands:
        d = _levenshtein(target, c)
        # Tighter shared-prefix bonus so siblings like ``stream_query`` rank
        # ahead of unrelated methods when the offender is ``stream_answer``.
        prefix = 0
        for a, b in zip(target, c, strict=False):
            if a == b:
                prefix += 1
            else:
                break
        scored.append((d - prefix, c))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [name for _, name in scored[:limit]]


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance — small inputs, no need for numpy."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def _suggest_method_alternatives(
    owner: str,
    method_name: str,
    lookup: StructuralIndexLookup,
    sibling_provides: dict[SymbolRef, Signature | None],
) -> list[str]:
    """All method names defined on `owner` (siblings ∪ disk source)."""
    methods: set[str] = set()
    for ref in sibling_provides:
        if ref.kind == "method" and ref.owner == owner and ref.name:
            methods.add(ref.name)
    for cls in lookup.classes.get(owner, []):
        for m in cls.methods:
            methods.add(m)
    return _close_method_matches(method_name, methods)


def _check_existence(
    reference: Reference,
    artifact_file: str,
    lookup: StructuralIndexLookup,
    sibling_provides: dict[SymbolRef, Signature | None],
) -> ClosureViolation | None:
    if _ref_in_codebase(reference.ref, lookup):
        return None
    for k in ("method", "field", "function", "class", "import_name"):
        alt = SymbolRef(owner=reference.ref.owner, name=reference.ref.name, kind=k)
        if alt in sibling_provides:
            return None
    # class-only ref satisfied if any sibling defines that class name
    if reference.ref.owner and not reference.ref.name:
        for sib in sibling_provides:
            if sib.owner == reference.ref.owner:
                return None

    detail = reference.context
    # For missing methods on a class that DOES exist (codebase or sibling),
    # surface the actual method names so the model can pick the right one.
    # Fixes the "method-name drift" class of bugs where one artifact calls
    # `engine.stream_answer` but the sibling engine artifact provides only
    # `stream_query` — pure spelling drift that's invisible per-artifact.
    if reference.ref.kind == "method" and reference.ref.owner and reference.ref.name:
        owner = reference.ref.owner
        owner_exists = lookup.class_exists(owner) or any(
            sib.owner == owner for sib in sibling_provides
        )
        if owner_exists:
            close = _suggest_method_alternatives(
                owner, reference.ref.name, lookup, sibling_provides
            )
            if close:
                joined = ", ".join(f"{owner}.{m}" for m in close)
                detail = (
                    f"{reference.context} — {owner}.{reference.ref.name} is not "
                    f"defined on {owner}. Closest matches: {joined}."
                )
            else:
                detail = (
                    f"{reference.context} — {owner}.{reference.ref.name} is not "
                    f"defined on {owner} (class has no methods of similar name)."
                )

    return ClosureViolation(
        artifact=artifact_file,
        ref=reference.ref,
        line=reference.line,
        context=reference.context,
        kind="missing" if reference.ref.kind != "import_name" else "import",
        detail=detail,
    )


def _check_usage(
    reference: Reference,
    artifact_file: str,
    lookup: StructuralIndexLookup,
    sibling_provides: dict[SymbolRef, Signature | None],
) -> ClosureViolation | None:
    """Check caller usage matches callee signature (async for, await, etc.)."""
    if reference.usage in ("call", "field_access", "import"):
        return None
    sig = _signature_of(reference.ref, lookup, sibling_provides)
    if sig is None:
        return None

    if reference.usage == "async_iter":
        if sig.is_async and sig.is_generator:
            return None
        if _is_async_iter_type(sig.return_type):
            return None
        if sig.is_generator or _looks_like_generator(sig.return_type):
            return ClosureViolation(
                artifact=artifact_file,
                ref=reference.ref,
                line=reference.line,
                context=reference.context,
                kind="usage",
                detail=(
                    f"async for on sync iterator — {reference.ref.pretty()} returns "
                    f"{sig.return_type or 'Iterator'} (sync). Use plain `for`, not `async for`."
                ),
            )
    elif reference.usage == "await":
        if sig.is_async and not sig.is_generator:
            return None
        if _is_awaitable_type(sig.return_type):
            return None
        return ClosureViolation(
            artifact=artifact_file,
            ref=reference.ref,
            line=reference.line,
            context=reference.context,
            kind="usage",
            detail=(
                f"await on non-awaitable — {reference.ref.pretty()} is not a coroutine "
                f"(return type: {sig.return_type or 'unknown'}). Remove `await`."
            ),
        )
    elif reference.usage == "iter":
        if sig.is_async and sig.is_generator:
            return ClosureViolation(
                artifact=artifact_file,
                ref=reference.ref,
                line=reference.line,
                context=reference.context,
                kind="usage",
                detail="plain `for` on async generator — use `async for`.",
            )
        if _is_async_iter_type(sig.return_type):
            return ClosureViolation(
                artifact=artifact_file,
                ref=reference.ref,
                line=reference.line,
                context=reference.context,
                kind="usage",
                detail="plain `for` on async iterable — use `async for`.",
            )
    return None


def _check_kwargs(
    reference: Reference,
    artifact_file: str,
    lookup: StructuralIndexLookup,
    sibling_provides: dict[SymbolRef, Signature | None],
) -> list[ClosureViolation]:
    """Check each kwarg name is in the callee's parameter list."""
    if not reference.kwargs:
        return []
    sig = _signature_of(reference.ref, lookup, sibling_provides)
    if sig is None or sig.has_var_keywords:
        return []
    if not sig.params:
        return []

    params = set(sig.params)
    violations: list[ClosureViolation] = []
    for kw in reference.kwargs:
        if kw not in params:
            violations.append(
                ClosureViolation(
                    artifact=artifact_file,
                    ref=reference.ref,
                    line=reference.line,
                    context=reference.context,
                    kind="kwargs",
                    detail=(
                        f"`{kw}` is not a parameter of {reference.ref.pretty()}"
                        f" (known: {', '.join(sorted(params))})"
                    ),
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Plan-level closure check (entry point)
# ---------------------------------------------------------------------------


def check_closure(
    artifacts: list[dict],
    lookup: StructuralIndexLookup,
    source_dir: str = "",
) -> list[ClosureViolation]:
    """Run all invariant checks across the artifact set.

    Returns violations sorted by kind (missing first, then usage, then kwargs).

    `source_dir` enables `self._attr` type tracking by reading target class
    `__init__` bodies from disk.
    """
    # Build the provides dict from all siblings.
    # When the caller annotates each artifact with `"strategy"` ("surgical" or
    # "new_code"), thread that into extract_provides so the heuristic doesn't
    # misclassify dedented surgical content as a top-level function (see B15).
    provides: dict[SymbolRef, Signature | None] = {}
    for art in artifacts:
        strategy = art.get("strategy")
        is_surgical: bool | None
        if strategy == "surgical":
            is_surgical = True
        elif strategy == "new_code":
            is_surgical = False
        else:
            is_surgical = None
        provides.update(
            extract_provides(
                art.get("content", ""),
                art.get("filename", ""),
                lookup,
                is_surgical=is_surgical,
            )
        )

    violations: list[ClosureViolation] = []
    for art in artifacts:
        filename = art.get("filename", "")
        content = art.get("content", "")

        disk_self_attrs = load_target_self_attrs(filename, source_dir, lookup)
        # Also absorb self-attr bindings from the artifact's own content —
        # matters when the target class is defined by the artifact itself
        # (so the disk file doesn't exist yet or is stale). Artifact
        # bindings represent the plan's target state, so they override
        # any stale disk values for the same attribute.
        artifact_self_attrs = extract_self_attrs_from_content(content, lookup)
        self_attrs: dict[str, str] = {}
        self_attrs.update(disk_self_attrs)
        self_attrs.update(artifact_self_attrs)

        refs = extract_references(
            content,
            filename,
            lookup,
            self_attrs=self_attrs,
            sibling_provides=provides,
        )

        for reference in refs:
            # 1. Existence
            v = _check_existence(reference, filename, lookup, provides)
            if v is not None:
                violations.append(v)
                continue
            # 2. Usage
            v = _check_usage(reference, filename, lookup, provides)
            if v is not None:
                violations.append(v)
            # 3. Kwargs (additive)
            violations.extend(_check_kwargs(reference, filename, lookup, provides))

    # Fix E: collapse cascading violations on fabricated owners.
    # When a parameter type annotation is a fabricated class (not in
    # codebase, not in siblings), every `param.field` access fires an
    # individual missing violation. That's noise — the root issue is the
    # fabricated parameter TYPE, not each field. Convert the cluster into
    # a single "missing class" violation pointing at the first occurrence,
    # so repair strategy 1 routes on the class and strategy 2 sees one
    # actionable error instead of 5-10 duplicates.
    violations = _dedupe_fabricated_owner_cascades(violations, lookup, provides)

    # Drop exact duplicates: a fabricated class referenced from both a
    # parameter annotation (via _emit_annotation_types) and a field cascade
    # (collapsed by Fix E) would otherwise yield two identical class
    # violations on the same artifact. Dedupe by (artifact, kind, ref).
    violations = _dedupe_exact(violations)

    order = {
        "missing": 0,
        "import": 1,
        "field": 2,
        "usage": 3,
        "kwargs": 4,
    }
    violations.sort(key=lambda v: (order.get(v.kind, 99), v.artifact, v.line))
    return violations


def _dedupe_exact(violations: list[ClosureViolation]) -> list[ClosureViolation]:
    """Drop exact-duplicate violations (same artifact, kind, ref)."""
    seen: set[tuple[str, str, str | None, str | None, str]] = set()
    out: list[ClosureViolation] = []
    for v in violations:
        key = (v.artifact, v.kind, v.ref.owner, v.ref.name, v.ref.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _dedupe_fabricated_owner_cascades(
    violations: list[ClosureViolation],
    lookup: StructuralIndexLookup,
    provides: dict[SymbolRef, Signature | None],
) -> list[ClosureViolation]:
    """Collapse per-field violations into one root-class violation when the owner is fabricated.

    A parameter annotation pointing at a class that doesn't exist produces
    one violation per field access on that param. Those aren't independent
    bugs — they're all symptoms of one bad annotation. Collapse them into
    a single violation naming the fabricated class, preserving the first
    artifact/line as the error site.
    """
    if not violations:
        return violations

    # Find owners that are themselves missing (fabricated classes).
    fabricated_owners: set[str] = set()
    for v in violations:
        if v.ref.kind == "field" and v.ref.owner:
            # Check if owner exists in codebase or siblings
            owner_exists = lookup.class_exists(v.ref.owner)
            if not owner_exists:
                for sib in provides:
                    if sib.owner == v.ref.owner:
                        owner_exists = True
                        break
            if not owner_exists:
                fabricated_owners.add(v.ref.owner)

    if not fabricated_owners:
        return violations

    deduped: list[ClosureViolation] = []
    # Track which fabricated owners we've already reported a root for
    seen_root_for_owner: dict[tuple[str, str], ClosureViolation] = {}
    for v in violations:
        if v.ref.owner in fabricated_owners and v.ref.kind == "field":
            # Cascading field access on a fabricated class — replace with
            # a single root missing-class violation (per-artifact).
            key = (v.artifact, v.ref.owner)
            if key in seen_root_for_owner:
                continue  # already emitted the root for this file
            root = ClosureViolation(
                artifact=v.artifact,
                ref=SymbolRef(owner=v.ref.owner, name=None, kind="class"),
                line=v.line,
                context=f"{v.ref.owner} (fabricated class used as parameter type — root of field cascade)",
                kind="missing",
                detail=(
                    f"Parameter type {v.ref.owner} does not exist in codebase "
                    f"or sibling artifacts. Use an existing schema class."
                ),
            )
            seen_root_for_owner[key] = root
            deduped.append(root)
        else:
            deduped.append(v)
    return deduped


# ---------------------------------------------------------------------------
# Routing: where should a missing symbol live?
# ---------------------------------------------------------------------------


def route_missing_symbol(
    violation: ClosureViolation,
    lookup: StructuralIndexLookup,
) -> str | None:
    """Given a missing-symbol violation, suggest the file that should own it.

    Returns None for usage/kwargs/import/field violations — those need
    regeneration, not expansion.
    """
    if violation.kind != "missing":
        return None
    ref = violation.ref
    if not ref.owner:
        return None
    classes = lookup.find_classes(ref.owner)
    if not classes:
        return None
    return classes[0].file
