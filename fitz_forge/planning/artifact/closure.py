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

import ast
import logging
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from fitz_forge.planning.validation.grounding import (
    _SKIP_NAMES as _GROUNDING_SKIP_NAMES,
)
from fitz_forge.planning.validation.grounding import (
    StructuralIndexLookup,
    extract_init_self_attrs,
    extract_type_name,
    try_parse,
)

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
    kind: str = "missing"  # "missing" | "usage" | "kwargs" | "import" | "field"
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
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    target = _target_class_for_file(filename, lookup)
    if not target:
        return {}

    known = set(lookup._all_class_names) if hasattr(lookup, "_all_class_names") else None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == target:
            return extract_init_self_attrs(node, known_classes=known)
    return {}


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
# Reference collector — walks an artifact AST, emits Reference objects
# ---------------------------------------------------------------------------


class _ReferenceCollector(ast.NodeVisitor):
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
    ) -> None:
        self.filename = filename
        self.lookup = lookup
        self.self_attrs = self_attrs or {}
        self.sibling_provides = sibling_provides or {}
        self.refs: list[Reference] = []
        self._scope_stack: list[dict[str, str]] = [{}]
        # Iterator/awaitable kind tracked per variable for async-for/await
        # propagation. Maps var name → (kind, originating_ref) where kind is
        # one of {"sync_iter", "async_iter", "awaitable"} and originating_ref
        # is the SymbolRef of the call that produced the value. Cleared on
        # function scope pop alongside type bindings.
        self._iter_kinds_stack: list[dict[str, tuple[str, SymbolRef, str]]] = [{}]

    # -- scope helpers ------------------------------------------------------

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

    def _lookup_iter_kind(
        self, name: str
    ) -> tuple[str, SymbolRef, str] | None:
        for scope in reversed(self._iter_kinds_stack):
            if name in scope:
                return scope[name]
        return None

    def _bind_iter_kind(
        self, name: str, kind: str, ref: SymbolRef, context: str
    ) -> None:
        self._iter_kinds_stack[-1][name] = (kind, ref, context)

    # -- emit helpers -------------------------------------------------------

    def _kwargs_of(self, call: ast.Call) -> frozenset[str]:
        return frozenset(k.arg for k in call.keywords if k.arg)

    def _emit(self, ref: Reference) -> None:
        self.refs.append(ref)

    def _emit_call(self, node: ast.Call, usage: str) -> None:
        """Emit a Reference for a Call node with the given usage kind."""
        line = getattr(node, "lineno", 0)
        kwargs = self._kwargs_of(node)
        func = node.func

        # Case: obj.method() where obj has a known type
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if isinstance(func.value, ast.Name):
                obj_name = func.value.id
                if obj_name == "self":
                    return  # self.method() — grounding handles it
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
            elif (
                isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
            ):
                # self._attr.method()
                attr_name = func.value.attr
                t = self.self_attrs.get(attr_name)
                if t and t not in _SKIP_NAMES:
                    self._emit(
                        Reference(
                            ref=SymbolRef(owner=t, name=attr, kind="method"),
                            line=line,
                            context=f"self.{attr_name}.{attr}()",
                            usage=usage,
                            kwargs=kwargs,
                        )
                    )
        # Case: ClassName(...) — class instantiation
        elif isinstance(func, ast.Name):
            name = func.id
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

    def _walk_call_args(self, call: ast.Call) -> None:
        """Walk args/kwargs without re-visiting .func (already handled)."""
        for arg in call.args:
            self.visit(arg)
        for kw in call.keywords:
            self.visit(kw.value)

    # -- visitors -----------------------------------------------------------

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._push_scope()
        for arg in node.args.args:
            if not arg.annotation:
                continue
            t = extract_type_name(arg.annotation)
            if t and t not in _SKIP_NAMES:
                self._bind(arg.arg, t)
        for stmt in node.body:
            self.visit(stmt)
        self._pop_scope()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_func(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            t = self._infer_rhs_type(node.value)
            if t:
                self._bind(var_name, t)
            # Track iterator/awaitable kind for async-for/await propagation:
            # when `var = typed_obj.method(...)` and method's return type is
            # an Iterator/AsyncIterator/Coroutine, record it so a later
            # `async for x in var` or `await var` can resolve back to the
            # originating call.
            kind_info = self._infer_rhs_iter_kind(node.value)
            if kind_info:
                kind, ref, context = kind_info
                self._bind_iter_kind(var_name, kind, ref, context)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            t = extract_type_name(node.annotation)
            if t and t not in _SKIP_NAMES:
                self._bind(node.target.id, t)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self._emit_call(node, usage="call")
        self._walk_call_args(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        if isinstance(node.iter, ast.Call):
            self._emit_call(node.iter, usage="async_iter")
            self._walk_call_args(node.iter)
        elif isinstance(node.iter, ast.Name):
            # Propagate usage back to the originating call when iterating a
            # variable that was bound to a method call result. Catches:
            #     stream = service.query_stream(...)   # bound as sync_iter
            #     async for x in stream: ...           # ← usage mismatch
            self._propagate_var_usage(
                node.iter.id,
                used_as="async_iter",
                line=getattr(node.iter, "lineno", 0),
            )
        else:
            self.visit(node.iter)
        self.visit(node.target)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        if isinstance(node.iter, ast.Call):
            self._emit_call(node.iter, usage="iter")
            self._walk_call_args(node.iter)
        elif isinstance(node.iter, ast.Name):
            self._propagate_var_usage(
                node.iter.id,
                used_as="iter",
                line=getattr(node.iter, "lineno", 0),
            )
        else:
            self.visit(node.iter)
        self.visit(node.target)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_Await(self, node: ast.Await) -> None:  # noqa: N802
        if isinstance(node.value, ast.Call):
            self._emit_call(node.value, usage="await")
            self._walk_call_args(node.value)
        elif isinstance(node.value, ast.Name):
            self._propagate_var_usage(
                node.value.id,
                used_as="await",
                line=getattr(node.value, "lineno", 0),
            )
        else:
            self.visit(node.value)

    def _propagate_var_usage(
        self,
        var_name: str,
        used_as: str,
        line: int,
    ) -> None:
        """Emit a Reference if `var_name` was bound to a call with a mismatched iter kind.

        When `var = obj.method(...)` was recorded with a return type kind
        (sync_iter / async_iter / awaitable), a later `async for`, `for`, or
        `await` on that variable re-emits the original call's SymbolRef with
        the new usage context so `_check_usage` catches the mismatch.
        """
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

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        # Bare attribute reads (not the .func of a Call).
        if isinstance(node.value, ast.Name):
            obj = node.value.id
            if obj == "self":
                self.generic_visit(node)
                return
            t = self._lookup_type(obj)
            if t and t not in _SKIP_NAMES:
                self._emit(
                    Reference(
                        ref=SymbolRef(owner=t, name=node.attr, kind="field"),
                        line=getattr(node, "lineno", 0),
                        context=f"{obj}.{node.attr}",
                        usage="field_access",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if not node.module:
            return
        top = node.module.split(".")[0]
        if top in _STDLIB_PACKAGES:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            self._emit(
                Reference(
                    ref=SymbolRef(owner=node.module, name=alias.name, kind="import_name"),
                    line=getattr(node, "lineno", 0),
                    context=f"from {node.module} import {alias.name}",
                    usage="import",
                )
            )

    def _infer_rhs_type(self, value: ast.expr) -> str | None:
        """Infer the type of an RHS expression for simple cases."""
        if isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Name):
                if func.id[:1].isupper() and func.id not in _SKIP_NAMES:
                    return func.id
                rt = _locator_return_type(func.id, self.lookup)
                if rt:
                    return rt
            elif isinstance(func, ast.Attribute):
                if func.attr[:1].isupper() and func.attr not in _SKIP_NAMES:
                    return func.attr
        return None

    def _infer_rhs_iter_kind(
        self, value: ast.expr
    ) -> tuple[str, SymbolRef, str] | None:
        """If RHS is a call whose return type is iterable/awaitable, return
        (kind, ref, context) for later usage propagation.

        Resolves the callee against sibling_provides first, then the
        codebase index. Works for:
            var = typed_obj.method(...)
            var = self._attr.method(...)
            var = top_level_func(...)
        """
        if not isinstance(value, ast.Call):
            return None
        ref_and_return = self._resolve_call_target(value)
        if ref_and_return is None:
            return None
        ref, return_type, context = ref_and_return
        if return_type is None:
            return None
        # Classify the return type into an iterator / awaitable kind.
        if _is_async_iter_type(return_type):
            return ("async_iter", ref, context)
        if _is_awaitable_type(return_type):
            return ("awaitable", ref, context)
        if _looks_like_generator(return_type):
            return ("sync_iter", ref, context)
        return None

    def _resolve_call_target(
        self, call: ast.Call
    ) -> tuple[SymbolRef, str | None, str] | None:
        """Resolve a Call to (SymbolRef of callee, return_type_string, context).

        Looks up the target method/function via sibling_provides first,
        then the codebase index. Returns None if the target can't be
        identified (e.g. bare local call, builtin).
        """
        func = call.func
        # Case: typed_obj.method(...)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            obj_name = func.value.id
            attr = func.attr
            if obj_name == "self":
                return None  # handled via self._attr case below
            obj_type = self._lookup_type(obj_name)
            if not obj_type or obj_type in _SKIP_NAMES:
                return None
            ref = SymbolRef(owner=obj_type, name=attr, kind="method")
            return_type = self._lookup_return_type(ref)
            return (ref, return_type, f"{obj_name}.{attr}()")

        # Case: self._attr.method(...)
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        ):
            attr_name = func.value.attr
            method = func.attr
            attr_type = self.self_attrs.get(attr_name)
            if not attr_type or attr_type in _SKIP_NAMES:
                return None
            ref = SymbolRef(owner=attr_type, name=method, kind="method")
            return_type = self._lookup_return_type(ref)
            return (ref, return_type, f"self.{attr_name}.{method}()")

        # Case: top_level_func(...)
        if isinstance(func, ast.Name):
            if func.id in _SKIP_NAMES or func.id[:1].isupper():
                return None
            ref = SymbolRef(owner=None, name=func.id, kind="function")
            # Check sibling provides
            sig = self.sibling_provides.get(ref)
            if sig and sig.return_type:
                return (ref, sig.return_type, f"{func.id}()")
            # Fallback to codebase
            funcs = self.lookup.find_function(func.id)
            if funcs:
                return (ref, funcs[0].return_type, f"{func.id}()")
            return None

        return None

    def _lookup_return_type(self, ref: SymbolRef) -> str | None:
        """Find return type of a method ref via siblings then codebase."""
        # Sibling provides (new artifacts being added in this plan)
        sig = self.sibling_provides.get(ref)
        if sig is None:
            # Try without exact kind match (field vs method)
            for k in ("method", "field", "function"):
                alt = SymbolRef(owner=ref.owner, name=ref.name, kind=k)
                sig = self.sibling_provides.get(alt)
                if sig is not None:
                    break
        if sig and sig.return_type:
            return sig.return_type
        # Codebase lookup via IndexedMethod
        if ref.owner and ref.name:
            for cls in self.lookup.classes.get(ref.owner, []):
                if ref.name in cls.methods:
                    return cls.methods[ref.name].return_type
        return None


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
    tree = try_parse(content)
    if tree is None:
        return []
    collector = _ReferenceCollector(
        filename,
        lookup,
        self_attrs=self_attrs,
        sibling_provides=sibling_provides,
    )
    collector.visit(tree)
    return collector.refs


# ---------------------------------------------------------------------------
# Provides: what an artifact contributes (symbols + signatures)
# ---------------------------------------------------------------------------


def _sig_from_funcdef(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Signature:
    params = [a.arg for a in node.args.args if a.arg != "self"]
    if node.args.vararg:
        params.append(f"*{node.args.vararg.arg}")
    for a in node.args.kwonlyargs:
        params.append(a.arg)
    has_var_kw = node.args.kwarg is not None
    ret: str | None = None
    if node.returns:
        try:
            ret = ast.unparse(node.returns)
        except Exception:
            pass
    has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))
    return Signature(
        params=params,
        return_type=ret,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_generator=has_yield,
        has_var_keywords=has_var_kw,
    )


def extract_provides(
    content: str,
    filename: str,
    lookup: StructuralIndexLookup,
) -> dict[SymbolRef, Signature | None]:
    """Extract symbols an artifact defines, keyed by SymbolRef with signatures.

    Returns a dict so usage checks can look up signatures. Class refs map to
    None; method/function refs map to their Signature.
    """
    out: dict[SymbolRef, Signature | None] = {}
    tree = try_parse(content)
    if tree is None:
        return out

    # Surgical rewrite: indented content where top level is a FunctionDef
    is_surgical = (
        content
        and content.lstrip() != content
        and any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.iter_child_nodes(tree)
        )
    )
    target_class = _target_class_for_file(filename, lookup) if is_surgical else None

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _sig_from_funcdef(node)
            if target_class:
                out[SymbolRef(owner=target_class, name=node.name, kind="method")] = sig
                out[SymbolRef(owner=target_class, name=node.name, kind="field")] = sig
            else:
                out[SymbolRef(owner=None, name=node.name, kind="function")] = sig
        elif isinstance(node, ast.ClassDef):
            out[SymbolRef(owner=node.name, name=None, kind="class")] = None
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = _sig_from_funcdef(child)
                    out[SymbolRef(owner=node.name, name=child.name, kind="method")] = sig
                    out[SymbolRef(owner=node.name, name=child.name, kind="field")] = sig
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    # Pydantic / dataclass fields
                    out[SymbolRef(owner=node.name, name=child.target.id, kind="field")] = None

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
            # MRO-aware field check includes pydantic / dataclass fields AND
            # property-decorated methods (which look like fields to callers).
            return lookup.class_has_field(ref.owner, ref.name)
        # For methods (and MRO-aware walk)
        return lookup.class_has_method(ref.owner, ref.name)
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
    return ClosureViolation(
        artifact=artifact_file,
        ref=reference.ref,
        line=reference.line,
        context=reference.context,
        kind="missing" if reference.ref.kind != "import_name" else "import",
        detail=reference.context,
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
    # Build the provides dict from all siblings
    provides: dict[SymbolRef, Signature | None] = {}
    for art in artifacts:
        provides.update(
            extract_provides(
                art.get("content", ""),
                art.get("filename", ""),
                lookup,
            )
        )

    violations: list[ClosureViolation] = []
    for art in artifacts:
        filename = art.get("filename", "")
        content = art.get("content", "")

        self_attrs = load_target_self_attrs(filename, source_dir, lookup)
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

    order = {"missing": 0, "import": 1, "field": 2, "usage": 3, "kwargs": 4}
    violations.sort(key=lambda v: (order.get(v.kind, 99), v.artifact, v.line))
    return violations


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
