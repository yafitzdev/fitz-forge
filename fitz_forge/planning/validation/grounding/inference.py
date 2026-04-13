# fitz_forge/planning/validation/grounding/inference.py
"""Codebase inference: learn what exists from AST walks.

Everything in this module answers some variant of "what does this code
contribute to the structural index?". Consumed by `index.py` during
`augment_from_source_dir`, and by `closure.py` when it needs to resolve
a symbol through a typed variable.

Strategies (all deterministic, no LLM):
    - Return type from body       — `return ClassName(...)` patterns
    - Return type from yields     — function with yield → Iterator[T]
    - Return type from docstring  — `Returns: ClassName ...` line
    - Class fields                — dataclass / pydantic / class-level annotations
    - self._attr types             — from `__init__` assignments
    - MRO walking                 — methods inherited from base classes

These are precision-oriented: when ambiguous, return None rather than
guess. A false positive creates a real bug; a missed inference just
leaves closure slightly less informed.
"""

from __future__ import annotations

import ast
import re
import textwrap

# ---------------------------------------------------------------------------
# Parse with recovery
# ---------------------------------------------------------------------------


def try_parse(content: str) -> ast.Module | None:
    """Parse Python with dedent + class-wrap recovery for surgical artifacts."""
    for attempt in (
        content,
        textwrap.dedent(content),
        "class _:\n    " + content.replace("\n", "\n    "),
    ):
        try:
            return ast.parse(attempt)
        except SyntaxError:
            continue
    return None


# ---------------------------------------------------------------------------
# Type annotation extraction
# ---------------------------------------------------------------------------


def extract_type_name(annotation: ast.expr | None) -> str | None:
    """Extract a primary class name from a type annotation.

    `ChatRequest` → ChatRequest
    `Optional[ChatRequest]` → ChatRequest
    `list[ChatRequest]` → ChatRequest
    `fitz.ChatRequest` → ChatRequest
    `Iterator[str]` → Iterator
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        slice_node = annotation.slice
        if isinstance(slice_node, ast.Tuple):
            if slice_node.elts:
                return extract_type_name(slice_node.elts[0])
        else:
            return extract_type_name(slice_node)
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # Forward reference: `"ChatRequest"`. Take the first identifier-looking token.
        m = re.match(r"^[A-Za-z_][A-Za-z_0-9]*", annotation.value.strip())
        return m.group(0) if m else None
    return None


def unparse_annotation(annotation: ast.expr | None) -> str | None:
    """Unparse an annotation to its source form, or None on failure."""
    if annotation is None:
        return None
    try:
        return ast.unparse(annotation)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Return type inference — body / yields / docstring / annotation
# ---------------------------------------------------------------------------


def infer_return_type(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_classes: set[str] | None = None,
) -> str | None:
    """Unified return type inference for a function.

    Strategy order (precision-oriented — first hit wins):
        1. Explicit `-> Type` annotation.
        2. `return ClassName(...)` / `return ClassName.from_x(...)` in the body.
        3. Function contains `yield` → `Iterator[T]` (or `AsyncIterator[T]`).
        4. Docstring `Returns: ClassName ...` line, verified against `known_classes`.

    `known_classes` is used to verify docstring-derived type names against
    the real index (prevents matching arbitrary capitalized words in prose).
    """
    # Strategy 1: explicit annotation
    ret = unparse_annotation(node.returns)
    if ret:
        return ret

    # Strategy 2: return statements in body
    ret = _infer_return_from_body(node)
    if ret:
        return ret

    # Strategy 3: yield → iterator
    ret = _infer_return_from_yields(node)
    if ret:
        return ret

    # Strategy 4: docstring
    if known_classes:
        ret = _infer_return_from_docstring(node, known_classes)
        if ret:
            return ret

    return None


def _infer_return_from_body(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Infer return type from `return ClassName(...)` patterns in body.

    Returns the class name if all return statements produce the same class,
    otherwise None. One wrong branch and we bail — precision over recall.
    """
    candidates: set[str] = set()
    for child in ast.walk(node):
        # Don't descend into nested functions
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
            continue
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        name = class_name_of_expr(child.value)
        if name is None:
            return None  # ambiguous return — bail
        candidates.add(name)
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _infer_return_from_yields(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """A function containing `yield` is an iterator. Report its wrapped type.

    `async def` with yield → AsyncIterator
    `def` with yield → Iterator
    """
    has_yield = False
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
            continue
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            has_yield = True
            break
    if not has_yield:
        return None
    return "AsyncIterator" if isinstance(node, ast.AsyncFunctionDef) else "Iterator"


_RETURNS_SECTION_RE = re.compile(
    r"(?:Returns?|Yields?)\s*:\s*\n?\s*([A-Z][A-Za-z_0-9]*)",
    re.MULTILINE,
)


def _infer_return_from_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_classes: set[str],
) -> str | None:
    """Parse `Returns: ClassName ...` from the function's docstring.

    Only matches if the candidate class name actually exists in the
    index (prevents false positives from prose).
    """
    doc = ast.get_docstring(node)
    if not doc:
        return None
    m = _RETURNS_SECTION_RE.search(doc)
    if not m:
        return None
    candidate = m.group(1)
    if candidate in known_classes:
        return candidate
    return None


def class_name_of_expr(expr: ast.expr) -> str | None:
    """Best-effort: what class does this expression produce?

    `ClassName(...)` → ClassName
    `module.ClassName(...)` → ClassName
    `ClassName.from_x(...)` → ClassName (classmethod convention)
    Anything else → None
    """
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name):
            if func.id[:1].isupper():
                return func.id
        elif isinstance(func, ast.Attribute):
            # module.ClassName(...) — take .attr if uppercase
            if func.attr[:1].isupper():
                return func.attr
            # ClassName.from_x(...) — value is a Name starting uppercase
            if isinstance(func.value, ast.Name) and func.value.id[:1].isupper():
                return func.value.id
    return None


# ---------------------------------------------------------------------------
# Class field extraction (pydantic / dataclass / class-level annotations)
# ---------------------------------------------------------------------------


def extract_class_fields(class_node: ast.ClassDef) -> dict[str, str]:
    """Extract field names and types from a class body.

    Catches:
      - Dataclass fields: `field_name: Type` or `field_name: Type = default`
      - Pydantic fields: same shape
      - Class-level `var: Type` annotations

    Skips:
      - Methods / properties
      - ClassVar declarations
      - Nested classes

    Returns {field_name: type_name}.
    """
    fields: dict[str, str] = {}
    for child in ast.iter_child_nodes(class_node):
        if not isinstance(child, ast.AnnAssign):
            continue
        if not isinstance(child.target, ast.Name):
            continue
        # Skip ClassVar — it's not an instance field
        ann_str = unparse_annotation(child.annotation) or ""
        if "ClassVar" in ann_str:
            continue
        type_name = extract_type_name(child.annotation)
        if type_name:
            fields[child.target.id] = type_name
    return fields


# ---------------------------------------------------------------------------
# self._attr type tracking — parse `__init__` for self assignments
# ---------------------------------------------------------------------------


def extract_init_self_attrs(
    class_node: ast.ClassDef,
    known_classes: set[str] | None = None,
) -> dict[str, str]:
    """Parse `self._x = Y(...)` in the class's __init__ and return {attr: type}.

    Three type sources:
      1. `self._x = param_name` where param_name is annotated in __init__
      2. `self._x = ClassName(...)` → inferred type ClassName
      3. `self._x: Type = ...` or class-level `_x: Type` annotation
    """
    attrs: dict[str, str] = {}

    # Pass 0: class-level `_x: Type` annotations (for classes that declare
    # instance attrs at class level without a value).
    for child in ast.iter_child_nodes(class_node):
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            t = extract_type_name(child.annotation)
            if t:
                attrs[child.target.id] = t

    # Find __init__
    init_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for child in class_node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__":
            init_node = child
            break
    if init_node is None:
        return attrs

    # Build param -> type map from __init__ annotations
    param_types: dict[str, str] = {}
    for arg in init_node.args.args:
        t = extract_type_name(arg.annotation)
        if t:
            param_types[arg.arg] = t

    # Walk init body for self.* assignments
    for stmt in ast.walk(init_node):
        # self._x: Type = expr  — annotated assign on self
        if isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                t = extract_type_name(stmt.annotation)
                if t:
                    attrs[target.attr] = t
            continue

        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            continue
        attr_name = target.attr
        rhs = stmt.value

        # self._x = some_param
        if isinstance(rhs, ast.Name) and rhs.id in param_types:
            attrs[attr_name] = param_types[rhs.id]
            continue
        # self._x = ClassName(...) / module.ClassName(...) / ClassName.from_x(...)
        if isinstance(rhs, ast.Call):
            cname = class_name_of_expr(rhs)
            if cname and (known_classes is None or cname in known_classes):
                attrs[attr_name] = cname

    return attrs
