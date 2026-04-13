# fitz_forge/planning/validation/grounding/check.py
"""Per-artifact AST grounding check.

`check_artifact` walks an artifact's AST and flags references to symbols
that don't exist in the codebase (per `StructuralIndexLookup`). This is
the older, per-artifact half of grounding validation — the plan-level
closure check in `fitz_forge/planning/artifact/closure.py` extends this
shape to the whole artifact set.

The `_SKIP_NAMES` frozenset here is the canonical list of symbols never
flagged (builtins, stdlib, typing, fastapi, pydantic, etc.). Closure
imports and extends it.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass
from typing import Any

from .index import IndexedClass, StructuralIndexLookup

# ---------------------------------------------------------------------------
# Violation
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    artifact: str
    line: int
    symbol: str
    kind: str  # missing_method, missing_class, missing_function, wrong_arity, wrong_field, parse_error, param_mismatch
    detail: str
    suggestion: str = ""


# ---------------------------------------------------------------------------
# Skip lists — names never counted as fabrications
# ---------------------------------------------------------------------------


_SKIP_NAMES = frozenset(
    {
        # Builtins, keywords, primitive types
        "None",
        "True",
        "False",
        "self",
        "cls",
        "super",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "type",
        "object",
        # Standard exceptions
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
        "AttributeError",
        "NotImplementedError",
        "StopIteration",
        "OSError",
        "IOError",
        "FileNotFoundError",
        "ImportError",
        "IndexError",
        "ConnectionError",
        "TimeoutError",
        "PermissionError",
        # Stdlib classes commonly constructed directly
        "ThreadPoolExecutor",
        "ProcessPoolExecutor",
        "Lock",
        "Event",
        "Thread",
        "Queue",
        "defaultdict",
        "Counter",
        "OrderedDict",
        "deque",
        "datetime",
        "timedelta",
        "timezone",
        "Decimal",
        "Enum",
        "IntEnum",
        "ABC",
        "abstractmethod",
        "contextmanager",
        "asynccontextmanager",
        "wraps",
        "partial",
        "reduce",
        "lru_cache",
        "cached_property",
        # Typing
        "TypeVar",
        "ParamSpec",
        "Protocol",
        "Generic",
        "ClassVar",
        "Final",
        "Literal",
        "Annotated",
        "NamedTuple",
        "TypedDict",
        "cast",
        "overload",
        "dataclass_transform",
        "Optional",
        "Union",
        "Any",
        "List",
        "Dict",
        "Set",
        "Tuple",
        "Iterator",
        "Generator",
        "AsyncGenerator",
        "AsyncIterator",
        "Callable",
        "Type",
        "Sequence",
        "Mapping",
        "Iterable",
        "Awaitable",
        "Coroutine",
        # Common stdlib functions (may appear as Call().func.id)
        "callable",
        "reversed",
        "property",
        "staticmethod",
        "classmethod",
        "vars",
        "dir",
        "globals",
        "locals",
        "exec",
        "eval",
        "compile",
        "breakpoint",
        "print",
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        "any",
        "all",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "hash",
        "id",
        "repr",
        "open",
        "iter",
        "next",
        # Stdlib modules frequently used as attribute roots
        "Path",
        "logging",
        "json",
        "re",
        "os",
        "sys",
        "time",
        "uuid",
        "asyncio",
        "functools",
        "itertools",
        # Dataclasses / pydantic
        "dataclass",
        "field",
        "dataclasses",
        "BaseModel",
        "Field",
        "ConfigDict",
        "model_validator",
        "field_validator",
        "computed_field",
        # FastAPI / Starlette
        "APIRouter",
        "Request",
        "StreamingResponse",
        "EventSourceResponse",
        "Depends",
        "HTTPException",
        "BackgroundTasks",
        "Response",
        "JSONResponse",
        "Body",
        "Header",
        "Cookie",
        "Form",
        "File",
        "UploadFile",
        "status",
        "router",
        "app",
    }
)


# ---------------------------------------------------------------------------
# Per-artifact check entry
# ---------------------------------------------------------------------------


def check_artifact(
    artifact: dict[str, Any],
    lookup: StructuralIndexLookup,
) -> list[Violation]:
    """Parse artifact content with AST and validate symbol references.

    Returns a list of Violations. Empty list means the artifact is grounded.
    """
    filename = artifact.get("filename", "unknown")
    content = artifact.get("content", "")

    if not content.strip():
        return []

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [
            Violation(
                filename,
                0,
                "",
                "parse_error",
                "Artifact is not valid Python — cannot validate",
            )
        ]

    violations: list[Violation] = []

    # Collect classes and functions defined IN this artifact
    artifact_classes: dict[str, set[str]] = {}
    artifact_functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = set()
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(child.name)
            artifact_classes[node.name] = methods
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            artifact_functions.add(node.name)

    # Match artifact file to the target class(es) in the structural index
    target_classes: list[IndexedClass] = []
    for cls_list in lookup.classes.values():
        for cls in cls_list:
            if cls.file == filename or filename.endswith(cls.file):
                target_classes.append(cls)

    # Build per-function var -> type maps from parameter annotations
    func_type_maps: dict[int, dict[str, str]] = {}
    func_nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        type_map: dict[str, str] = {}
        for arg in node.args.args:
            if arg.annotation and isinstance(arg.annotation, ast.Name):
                type_map[arg.arg] = arg.annotation.id
            elif arg.annotation and isinstance(arg.annotation, ast.Attribute):
                type_map[arg.arg] = arg.annotation.attr
        if type_map:
            func_type_maps[id(node)] = type_map
            func_nodes.append(node)

    # Per-function body membership lookup
    func_body_nodes: dict[int, set[int]] = {}
    for func_node in func_nodes:
        child_ids = set()
        for child in ast.walk(func_node):
            child_ids.add(id(child))
        func_body_nodes[id(func_node)] = child_ids

    for node in ast.walk(tree):
        var_type_map: dict[str, str] | None = None
        for func_node in func_nodes:
            if id(node) in func_body_nodes[id(func_node)]:
                var_type_map = func_type_maps.get(id(func_node))
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
    node: ast.AST,
    filename: str,
    lookup: StructuralIndexLookup,
    artifact_classes: dict[str, set[str]],
    target_classes: list[IndexedClass],
    violations: list[Violation],
    var_type_map: dict[str, str] | None = None,
    artifact_functions: set[str] | None = None,
) -> None:
    line = getattr(node, "lineno", 0)

    # self.method() calls
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    ):
        method_name = node.func.attr
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

    # Standalone function calls
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func_name = node.func.id
        if func_name in _SKIP_NAMES:
            return
        if func_name[0].isupper():
            if not lookup.class_exists(func_name):
                if func_name not in artifact_classes:
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
        else:
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
            else:
                expected_params = lookup.function_params(func_name)
                if expected_params is not None:
                    actual_args = len(node.args) + len(node.keywords)
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

    # Attribute access on typed local variables (obj.field)
    elif (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id not in _SKIP_NAMES
        and node.value.id != "self"
    ):
        var_name = node.value.id
        attr_name = node.attr
        type_name = (var_type_map or {}).get(var_name)
        if not type_name and var_name[0].islower():
            return
        if type_name:
            cls = lookup.find_class(type_name)
            if cls and cls.methods:
                # Use MRO-aware check: method OR field on class or ancestor
                if (
                    not lookup.class_has_method(type_name, attr_name)
                    and not lookup.class_has_field(type_name, attr_name)
                    and not attr_name.startswith("__")
                ):
                    known = sorted(cls.methods.keys())
                    suggestions = difflib.get_close_matches(
                        attr_name,
                        known,
                        n=3,
                        cutoff=0.5,
                    )
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


def check_all_artifacts(
    artifacts: list[dict[str, Any]],
    structural_index: str,
) -> list[Violation]:
    """Run AST grounding check + parallel-method signature check on a set."""
    if not artifacts:
        return []

    lookup = StructuralIndexLookup(structural_index)
    all_violations: list[Violation] = []
    for artifact in artifacts:
        all_violations.extend(check_artifact(artifact, lookup))
    all_violations.extend(_check_parallel_signatures(artifacts, lookup))
    return all_violations


_PARALLEL_SUFFIXES = ("_stream", "_async", "_streaming", "stream_")


def _check_parallel_signatures(
    artifacts: list[dict[str, Any]],
    lookup: StructuralIndexLookup,
) -> list[Violation]:
    """Check that parallel methods (e.g. generate_stream) match the original's params."""
    violations: list[Violation] = []

    for artifact in artifacts:
        content = artifact.get("content", "")
        filename = artifact.get("filename", "unknown")
        if not content.strip():
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method_name = node.name
            original_name = None
            for suffix in _PARALLEL_SUFFIXES:
                if method_name.endswith(suffix):
                    original_name = method_name[: -len(suffix)]
                    break
                if method_name.startswith(suffix):
                    original_name = method_name[len(suffix) :]
                    break
            if not original_name:
                continue

            new_params = [a.arg for a in node.args.args if a.arg != "self"]
            original_funcs = lookup.find_function(original_name)
            if original_funcs:
                orig_params = original_funcs[0].params
                if len(new_params) < len(orig_params) - 2:
                    violations.append(
                        Violation(
                            artifact=filename,
                            line=node.lineno,
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
