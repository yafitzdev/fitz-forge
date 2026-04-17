# fitz_forge/planning/artifact/validate.py
"""Output validation for generated artifacts.

Every check is deterministic — no LLM calls. Returns a list of
ArtifactError objects. Empty list = artifact is valid.

These are the SAME checks the V2 scorer runs, so if an artifact
passes validation here, the scorer will agree.
"""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..validation.grounding.inference import (
    _class_body,
    _rightmost_attribute_name,
    _unwrap_decorated,
    iter_all_classes,
)
from ..validation.grounding.parser import parse_python
from .context import ArtifactContext

if TYPE_CHECKING:
    from tree_sitter import Node

logger = logging.getLogger(__name__)

_YIELD_RE = re.compile(r"\byield\b")
_NOT_IMPLEMENTED_RE = re.compile(r"raise\s+NotImplementedError")
_STREAMING_INDICATORS = ("engine.py", "synthesizer.py")
_ITERATOR_TYPES = ("Iterator", "Generator", "AsyncIterator", "AsyncGenerator")


@dataclass
class ArtifactError:
    """A specific validation failure with actionable fix suggestion."""

    check: str  # "parseable", "fabrication", "yield", "return_type", "empty"
    message: str  # human-readable error
    suggestion: str  # what to fix


_DATA_BASES = frozenset(
    {
        "BaseModel",
        "Enum",
        "IntEnum",
        "StrEnum",
        "Flag",
        "IntFlag",
        "TypedDict",
        "NamedTuple",
    }
)
_DATA_DECORATORS = frozenset(
    {"dataclass", "pydantic_dataclass", "attr.s", "attrs", "define"}
)


def _decorator_name(dec_node: "Node") -> str | None:
    """Return the leaf identifier of a ``decorator`` node."""
    body: Node | None = None
    for c in dec_node.children:
        if c.is_named:
            body = c
            break
    if body is None:
        return None
    if body.type == "identifier":
        return body.text.decode("utf-8")
    if body.type == "attribute":
        return _rightmost_attribute_name(body)
    if body.type == "call":
        callee = None
        for c in body.children:
            if c.is_named and c.type != "argument_list":
                callee = c
                break
        if callee is None:
            return None
        if callee.type == "identifier":
            return callee.text.decode("utf-8")
        if callee.type == "attribute":
            return _rightmost_attribute_name(callee)
    return None


def _class_decorators(class_def: "Node") -> list[str]:
    """Return decorator leaf names for a class node."""
    parent = class_def.parent
    if parent is not None and parent.type == "decorated_definition":
        out: list[str] = []
        for c in parent.children:
            if c.type == "decorator":
                name = _decorator_name(c)
                if name:
                    out.append(name)
        return out
    return []


def _is_data_class(class_def: "Node") -> bool:
    """True iff the class is Pydantic / dataclass / Enum / TypedDict / annotated.

    Short-circuits in order: annotated field → base class → decorator.
    """
    body = _class_body(class_def)
    if body is not None:
        for stmt in body.children:
            if stmt.type != "expression_statement":
                continue
            inner = next((c for c in stmt.children if c.is_named), None)
            if inner is None or inner.type != "assignment":
                continue
            has_type = any(c.type == "type" for c in inner.children)
            if has_type:
                return True

    args = next((c for c in class_def.children if c.type == "argument_list"), None)
    if args is not None:
        for c in args.children:
            if not c.is_named:
                continue
            name: str | None = None
            if c.type == "identifier":
                name = c.text.decode("utf-8")
            elif c.type == "attribute":
                name = _rightmost_attribute_name(c)
            if name in _DATA_BASES:
                return True

    for d in _class_decorators(class_def):
        if d in _DATA_DECORATORS:
            return True

    return False


def _iter_all_functions(root: "Node"):
    """Yield every function_definition in the tree (nested and decorated)."""
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


def _check_parseable(content: str) -> ArtifactError | None:
    """Check if content is valid Python (with recovery)."""
    if parse_python(content) is None:
        return ArtifactError(
            check="parseable",
            message="Content is not valid Python (even after quote fix/dedent/class wrap recovery)",
            suggestion="Ensure the output is syntactically valid Python code",
        )
    return None


def _check_empty(content: str) -> ArtifactError | None:
    """Check if content has actual code.

    Accepts files with function/method defs OR with a data-model class
    (Pydantic BaseModel, dataclass, Enum, TypedDict, plain class with
    annotated fields). Schema files are valid Python but contain no defs.
    """
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    code_lines = [line for line in lines if not line.startswith("#") and not line.startswith('"""')]
    if len(code_lines) < 2:
        return ArtifactError(
            check="empty",
            message="Content has no meaningful code (fewer than 2 non-comment lines)",
            suggestion="Write the actual implementation, not just comments or stubs",
        )

    tree = parse_python(content)
    if tree is not None:
        root = tree.root_node
        for _fn in _iter_all_functions(root):
            return None
        for cls in iter_all_classes(root):
            if _is_data_class(cls):
                return None
        return ArtifactError(
            check="empty",
            message="Content has no function/method defs and no data-model class",
            suggestion="Include at least one function/method, or a Pydantic/dataclass/Enum class with annotated fields",
        )

    # Unparseable (or non-Python) — use language-agnostic text heuristics.
    _DEF_KEYWORDS = (
        "def ",
        "class ",
        "function ",
        "func ",
        "fn ",
        "async ",
        "export ",
        "const ",
        "let ",
        "var ",
        "model ",
        "interface ",
        "enum ",
        "struct ",
        "impl ",
        "pub fn ",
        "public ",
        "private ",
        "protected ",
    )
    if not any(kw in line for line in code_lines for kw in _DEF_KEYWORDS):
        return ArtifactError(
            check="empty",
            message="Content has no function or class definitions",
            suggestion="Include at least one function/method or class",
        )
    return None


def _check_fabrication(
    content: str,
    ctx: ArtifactContext,
) -> list[ArtifactError]:
    """Check for fabricated method calls using the structural index."""
    if not ctx.structural_index:
        return []

    from fitz_forge.planning.validation.grounding import (
        StructuralIndexLookup,
        check_artifact,
    )

    lookup = StructuralIndexLookup(ctx.structural_index)
    if ctx.source_dir:
        lookup.augment_from_source_dir(ctx.source_dir)

    # Try with original content first, then dedented — surgical rewrites
    # produce indented method bodies that check_artifact can't parse raw.
    violations = check_artifact({"filename": ctx.filename, "content": content}, lookup)
    if len(violations) == 1 and violations[0].kind == "parse_error":
        dedented = textwrap.dedent(content)
        violations = check_artifact({"filename": ctx.filename, "content": dedented}, lookup)

    errors = []
    for v in violations:
        if v.kind == "parse_error":
            continue  # handled by _check_parseable
        errors.append(
            ArtifactError(
                check="fabrication",
                message=f"{v.kind}: {v.symbol} — {v.detail}",
                suggestion=v.suggestion or f"Remove or replace {v.symbol}",
            )
        )
    return errors


def _check_yield(content: str, ctx: ArtifactContext) -> ArtifactError | None:
    """Check that streaming artifacts use yield."""
    is_streaming = any(ctx.filename.endswith(ind) for ind in _STREAMING_INDICATORS)
    if not is_streaming:
        return None

    # Check if purpose implies streaming
    purpose_lower = ctx.purpose.lower()
    streaming_words = ("stream", "yield", "generator", "token-by-token", "iterator")
    if not any(w in purpose_lower for w in streaming_words):
        return None

    if not _YIELD_RE.search(content):
        return ArtifactError(
            check="yield",
            message="Streaming method has no yield statements — this produces blocking output, not a stream",
            suggestion="Replace 'return Answer(...)' with 'yield token' to produce a generator",
        )
    return None


def _check_return_type(content: str, ctx: ArtifactContext) -> ArtifactError | None:
    """Check that streaming methods have Iterator/Generator return types."""
    is_streaming = any(ctx.filename.endswith(ind) for ind in _STREAMING_INDICATORS)
    if not is_streaming:
        return None

    tree = parse_python(content)
    if tree is None:
        return None  # handled by parseable check
    for fn in _iter_all_functions(tree.root_node):
        name_node = next((c for c in fn.children if c.type == "identifier"), None)
        if name_node is None:
            continue
        name = name_node.text.decode("utf-8")
        if "stream" not in name.lower():
            continue
        # Return annotation: ``type`` node between parameters and ``:``
        saw_params = False
        ret_node: Node | None = None
        for c in fn.children:
            if c.type == "parameters":
                saw_params = True
                continue
            if saw_params and c.type == "type":
                ret_node = c
                break
        if ret_node is None:
            continue
        named = [c for c in ret_node.children if c.is_named]
        ret_text = (named[0] if len(named) == 1 else ret_node).text.decode("utf-8")
        if not any(t in ret_text for t in _ITERATOR_TYPES):
            return ArtifactError(
                check="return_type",
                message=f"Method '{name}' returns '{ret_text}' but streaming methods must return Iterator/Generator",
                suggestion="Change return type to Iterator[str] or Generator[str, None, None]",
            )
    return None


def _check_not_implemented(content: str) -> ArtifactError | None:
    """Check for NotImplementedError stubs. Soft fail — warn only."""
    if _NOT_IMPLEMENTED_RE.search(content):
        return ArtifactError(
            check="not_implemented",
            message="Contains 'raise NotImplementedError' — this is a stub, not an implementation",
            suggestion="Implement the actual logic instead of raising NotImplementedError",
        )
    return None


def _is_python_file(filename: str) -> bool:
    """True if the filename looks like a Python source file."""
    return filename.endswith(".py") or not any(
        filename.endswith(ext)
        for ext in (".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".prisma")
    )


def validate(content: str, ctx: ArtifactContext) -> list[ArtifactError]:
    """Run all validation checks. Empty list = valid artifact.

    Checks are ordered by severity — parseable first (blocks everything),
    then structural checks, then semantic checks. For non-Python files,
    Python AST checks are skipped (parseable, fabrication) and only
    language-agnostic text heuristics apply (empty, not_implemented).
    """
    errors: list[ArtifactError] = []
    is_python = _is_python_file(ctx.filename)

    # Hard fails
    if is_python:
        err = _check_parseable(content)
        if err:
            errors.append(err)
            return errors

    err = _check_empty(content)
    if err:
        errors.append(err)
        return errors

    # Structural checks (Python AST-based — skip for non-Python)
    if is_python:
        errors.extend(_check_fabrication(content, ctx))

    # Semantic checks (streaming-specific, Python AST-based)
    if is_python:
        err = _check_yield(content, ctx)
        if err:
            errors.append(err)

        err = _check_return_type(content, ctx)
    if err:
        errors.append(err)

    # Soft checks (warn but don't block)
    err = _check_not_implemented(content)
    if err:
        errors.append(err)

    return errors
