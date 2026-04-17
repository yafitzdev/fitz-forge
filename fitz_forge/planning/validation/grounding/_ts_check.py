# fitz_forge/planning/validation/grounding/_ts_check.py
"""Tree-sitter implementation of check.py's grounding validator.

Mirrors ``check_artifact``, ``_check_node`` and
``_check_parallel_signatures`` node-for-node. The entry points in
check.py route here via ``grounding.index.get_engine() == "tree_sitter"``.
"""

from __future__ import annotations

import difflib
from collections import deque
from typing import TYPE_CHECKING, Any

from ._ts_inference import (
    _callable_of,
    _function_name,
    _rightmost_attribute_name,
    _unwrap_decorated,
    iter_all_classes,
    iter_class_methods,
)
from ._ts_parser import parse_python
from .check import _SKIP_NAMES, Violation
from .index import IndexedClass, StructuralIndexLookup

if TYPE_CHECKING:
    from tree_sitter import Node


def _iter_all_functions(root: "Node"):
    """Yield every function_definition in the tree (nested + decorated).

    Matches ``ast.walk`` filtered to FunctionDef/AsyncFunctionDef.
    """
    stack: list[Node] = [root]
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if n.type == "function_definition" and n.id not in seen:
            seen.add(n.id)
            yield n
        elif n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type == "function_definition" and inner.id not in seen:
                seen.add(inner.id)
                yield inner
        stack.extend(n.children)


def _walk_bfs(root: "Node"):
    """BFS traversal — matches ``ast.walk``'s visit order."""
    q: deque[Node] = deque([root])
    while q:
        n = q.popleft()
        yield n
        q.extend(n.children)


def _param_type_name(type_node: "Node") -> str | None:
    """Extract name for a parameter annotation, ast-style.

    ast version only populates the var_type_map when the annotation is
    ``ast.Name`` or ``ast.Attribute`` (not Subscript). We mirror that:
    only ``identifier`` or ``attribute`` under the ``type`` wrapper.
    """
    named = [c for c in type_node.children if c.is_named]
    if len(named) != 1:
        return None
    inner = named[0]
    if inner.type == "identifier":
        return inner.text.decode("utf-8")
    if inner.type == "attribute":
        return _rightmost_attribute_name(inner)
    return None


def _collect_param_type_map(func_def: "Node") -> dict[str, str]:
    """Return {param_name: type_name} for a function's typed_parameters.

    Only the ast subset (Name / Attribute annotations); Subscript
    annotations don't enter the map, matching the ast version.
    """
    params = next((c for c in func_def.children if c.type == "parameters"), None)
    if params is None:
        return {}
    out: dict[str, str] = {}
    for p in params.children:
        if p.type != "typed_parameter":
            continue
        ident = next((c for c in p.children if c.type == "identifier"), None)
        tnode = next((c for c in p.children if c.type == "type"), None)
        if ident is None or tnode is None:
            continue
        tname = _param_type_name(tnode)
        if tname:
            out[ident.text.decode("utf-8")] = tname
    return out


def _all_descendant_ids(node: "Node") -> set[int]:
    """Return the set of ``node.id`` values for node + every descendant."""
    out: set[int] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        out.add(n.id)
        stack.extend(n.children)
    return out


def _count_call_args(call: "Node") -> int:
    """Count actual arguments in a ``call`` node (positional + keyword)."""
    args_list = next((c for c in call.children if c.type == "argument_list"), None)
    if args_list is None:
        return 0
    n = 0
    for c in args_list.children:
        if c.is_named:
            n += 1
    return n


def _param_names_no_self(func_def: "Node") -> list[str]:
    """Flat parameter names, excluding ``self``. Matches ast version."""
    params = next((c for c in func_def.children if c.type == "parameters"), None)
    if params is None:
        return []
    out: list[str] = []
    for p in params.children:
        if p.type == "identifier":
            name = p.text.decode("utf-8")
        elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is None:
                continue
            name = ident.text.decode("utf-8")
        else:
            continue
        if name != "self":
            out.append(name)
    return out


def check_artifact(
    artifact: dict[str, Any],
    lookup: StructuralIndexLookup,
) -> list[Violation]:
    """Tree-sitter port of ``check.check_artifact``."""
    filename = artifact.get("filename", "unknown")
    content = artifact.get("content", "")

    if not content.strip():
        return []

    tree = parse_python(content)
    if tree is None:
        return [
            Violation(
                filename,
                0,
                "",
                "parse_error",
                "Artifact is not valid Python — cannot validate",
            )
        ]

    root = tree.root_node
    violations: list[Violation] = []

    # Artifact-defined classes (with their directly-declared methods)
    artifact_classes: dict[str, set[str]] = {}
    artifact_functions: set[str] = set()
    for cls in iter_all_classes(root):
        name_node = next((c for c in cls.children if c.type == "identifier"), None)
        if name_node is None:
            continue
        methods: set[str] = set()
        for m in iter_class_methods(cls):
            mname = _function_name(m)
            if mname:
                methods.add(mname)
        artifact_classes[name_node.text.decode("utf-8")] = methods

    for fn in _iter_all_functions(root):
        name = _function_name(fn)
        if name:
            artifact_functions.add(name)

    # Target classes in the structural index that map to this file
    target_classes: list[IndexedClass] = []
    for cls_list in lookup.classes.values():
        for cls in cls_list:
            if cls.file == filename or filename.endswith(cls.file):
                target_classes.append(cls)

    # Per-function typed-parameter map + descendant id set (for scope)
    func_type_maps: dict[int, dict[str, str]] = {}
    func_body_ids: dict[int, set[int]] = {}
    for fn in _iter_all_functions(root):
        tmap = _collect_param_type_map(fn)
        if tmap:
            func_type_maps[fn.id] = tmap
            func_body_ids[fn.id] = _all_descendant_ids(fn)

    for node in _walk_bfs(root):
        var_type_map: dict[str, str] | None = None
        for fid, body_ids in func_body_ids.items():
            if node.id in body_ids:
                var_type_map = func_type_maps.get(fid)
                break
        _check_node(
            node,
            filename,
            lookup,
            artifact_classes,
            target_classes,
            violations,
            var_type_map,
            artifact_functions,
        )

    return violations


def _check_node(
    node: "Node",
    filename: str,
    lookup: StructuralIndexLookup,
    artifact_classes: dict[str, set[str]],
    target_classes: list[IndexedClass],
    violations: list[Violation],
    var_type_map: dict[str, str] | None,
    artifact_functions: set[str] | None,
) -> None:
    line = node.start_point[0] + 1  # 1-indexed, matches ast.lineno

    if node.type == "call":
        callee = _callable_of(node)
        if callee is None:
            return

        # self.method()
        if callee.type == "attribute":
            idents = [c for c in callee.children if c.type == "identifier"]
            if len(idents) == 2 and idents[0].text.decode("utf-8") == "self":
                method_name = idents[1].text.decode("utf-8")
                if method_name.startswith("__"):
                    return
                for cls_methods in artifact_classes.values():
                    if method_name in cls_methods:
                        return
                for cls in target_classes:
                    if method_name in cls.methods:
                        return
                if not lookup.method_exists_anywhere(method_name):
                    suggestions = lookup.suggest_method(method_name)
                    violations.append(
                        Violation(
                            filename,
                            line,
                            f"self.{method_name}()",
                            "missing_method",
                            f"Method '{method_name}' not found on any class in the target file "
                            f"or anywhere in the codebase index",
                            f"Did you mean: {', '.join(suggestions)}" if suggestions else "",
                        )
                    )
            return

        # Bare function call
        if callee.type == "identifier":
            func_name = callee.text.decode("utf-8")
            if func_name in _SKIP_NAMES:
                return
            if func_name[0].isupper():
                if not lookup.class_exists(func_name) and func_name not in artifact_classes:
                    suggestions = lookup.suggest_class(func_name)
                    violations.append(
                        Violation(
                            filename,
                            line,
                            func_name,
                            "missing_class",
                            f"Class '{func_name}' not found in codebase index",
                            f"Did you mean: {', '.join(suggestions)}" if suggestions else "",
                        )
                    )
                return

            if artifact_functions and func_name in artifact_functions:
                return
            if not lookup.function_exists(func_name):
                if not any(func_name.startswith(p) for p in ("_", "pytest")):
                    suggestions = lookup.suggest_function(func_name)
                    if suggestions:
                        violations.append(
                            Violation(
                                filename,
                                line,
                                f"{func_name}()",
                                "missing_function",
                                f"Function '{func_name}' not found in codebase index",
                                f"Did you mean: {', '.join(suggestions)}",
                            )
                        )
                return

            expected_params = lookup.function_params(func_name)
            if expected_params is None:
                return
            has_varargs = any(p.startswith("*") for p in expected_params)
            if has_varargs:
                return
            actual_args = _count_call_args(node)
            expected = len(expected_params)
            if actual_args > 0 and expected > 0 and abs(actual_args - expected) > 2:
                violations.append(
                    Violation(
                        filename,
                        line,
                        f"{func_name}()",
                        "wrong_arity",
                        f"Called with {actual_args} args but index shows "
                        f"{expected} params: ({', '.join(expected_params)})",
                    )
                )
        return

    # Attribute access on typed locals (obj.field)
    if node.type == "attribute":
        idents = [c for c in node.children if c.type == "identifier"]
        if len(idents) != 2:
            return
        var_name = idents[0].text.decode("utf-8")
        attr_name = idents[1].text.decode("utf-8")
        if var_name in _SKIP_NAMES or var_name == "self":
            return
        type_name = (var_type_map or {}).get(var_name)
        if not type_name and var_name[0].islower():
            return
        if not type_name:
            return
        cls = lookup.find_class(type_name)
        if not cls or not cls.methods:
            return
        if attr_name.startswith("__"):
            return
        if lookup.class_has_method(type_name, attr_name):
            return
        if lookup.class_has_field(type_name, attr_name):
            return
        known = sorted(cls.methods.keys())
        suggestions = difflib.get_close_matches(attr_name, known, n=3, cutoff=0.5)
        violations.append(
            Violation(
                filename,
                line,
                f"{var_name}.{attr_name}",
                "wrong_field",
                f"'{attr_name}' not found on {type_name} "
                f"(known: {', '.join(known[:10])})",
                f"Did you mean: {', '.join(suggestions)}" if suggestions else "",
            )
        )


_PARALLEL_SUFFIXES = ("_stream", "_async", "_streaming", "stream_")


def check_parallel_signatures(
    artifacts: list[dict[str, Any]],
    lookup: StructuralIndexLookup,
) -> list[Violation]:
    """Tree-sitter port of ``check._check_parallel_signatures``."""
    violations: list[Violation] = []
    for artifact in artifacts:
        content = artifact.get("content", "")
        filename = artifact.get("filename", "unknown")
        if not content.strip():
            continue
        tree = parse_python(content)
        if tree is None:
            continue
        for fn in _iter_all_functions(tree.root_node):
            method_name = _function_name(fn)
            if method_name is None:
                continue
            original_name: str | None = None
            for suffix in _PARALLEL_SUFFIXES:
                if method_name.endswith(suffix):
                    original_name = method_name[: -len(suffix)]
                    break
                if method_name.startswith(suffix):
                    original_name = method_name[len(suffix) :]
                    break
            if not original_name:
                continue
            new_params = _param_names_no_self(fn)
            original_funcs = lookup.find_function(original_name)
            if not original_funcs:
                continue
            orig_params = original_funcs[0].params
            if len(new_params) < len(orig_params) - 2:
                violations.append(
                    Violation(
                        artifact=filename,
                        line=fn.start_point[0] + 1,
                        symbol=method_name,
                        kind="param_mismatch",
                        detail=(
                            f"Parallel method {method_name}() has {len(new_params)} params "
                            f"but original {original_name}() has {len(orig_params)}: "
                            f"({', '.join(orig_params)}). "
                            f"Parallel methods should accept the same parameters."
                        ),
                        suggestion=f"Add missing params from {original_name}()",
                    )
                )
    return violations
