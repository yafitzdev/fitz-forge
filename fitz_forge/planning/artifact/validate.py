# fitz_forge/planning/artifact/validate.py
"""Output validation for generated artifacts.

Every check is deterministic — no LLM calls. Returns a list of
ArtifactError objects. Empty list = artifact is valid.

These are the SAME checks the V2 scorer runs, so if an artifact
passes validation here, the scorer will agree.
"""

import ast
import logging
import re
import textwrap
from dataclasses import dataclass

from .context import ArtifactContext

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


def _try_parse(content: str) -> ast.Module | None:
    """Try parsing with recovery: raw -> dedent -> class wrap -> import-split."""
    from fitz_forge.planning.validation.grounding.inference import try_parse

    return try_parse(content)


def _check_parseable(content: str) -> ArtifactError | None:
    """Check if content is valid Python (with recovery)."""
    if _try_parse(content) is None:
        return ArtifactError(
            check="parseable",
            message="Content is not valid Python (even after quote fix/dedent/class wrap recovery)",
            suggestion="Ensure the output is syntactically valid Python code",
        )
    return None


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


def _is_data_class(node: ast.ClassDef) -> bool:
    """True if `node` is a Pydantic / dataclass / Enum / TypedDict style class.

    These are valid Python artifacts with no `def` — they contain only
    annotated fields or enum values, and should not trip the empty check.
    """
    # Any annotated field (pydantic / dataclass / plain class with annotations)
    for child in node.body:
        if isinstance(child, ast.AnnAssign):
            return True
    # Inherits from a data-model base
    for base in node.bases:
        name: str | None = None
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        if name in _DATA_BASES:
            return True
    # @dataclass / @pydantic.dataclass / @attr.s / @define decorator
    for dec in node.decorator_list:
        name = None
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                name = dec.func.id
            elif isinstance(dec.func, ast.Attribute):
                name = dec.func.attr
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name in _DATA_DECORATORS:
            return True
    # Enum-style class with plain assignments (e.g. `FOO = "foo"`)
    for child in node.body:
        if isinstance(child, ast.Assign) and any(
            isinstance(t, ast.Name) for t in child.targets
        ):
            # Only enough if inherits from an Enum — covered above. Don't
            # over-accept plain constants.
            pass
    return False


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
    tree = _try_parse(content)
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return None
            if isinstance(node, ast.ClassDef) and _is_data_class(node):
                return None
        return ArtifactError(
            check="empty",
            message="Content has no function/method defs and no data-model class",
            suggestion="Include at least one function/method, or a Pydantic/dataclass/Enum class with annotated fields",
        )
    # Unparseable (or non-Python) — use language-agnostic text heuristics.
    # Accept if content has definition-like keywords from any common language.
    _DEF_KEYWORDS = (
        "def ", "class ", "function ", "func ", "fn ",
        "async ", "export ", "const ", "let ", "var ",
        "model ", "interface ", "enum ", "struct ", "impl ",
        "pub fn ", "public ", "private ", "protected ",
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

    tree = _try_parse(content)
    if tree is None:
        return None  # handled by parseable check

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "stream" not in node.name.lower():
            continue
        if node.returns is None:
            continue

        ret_type = ast.unparse(node.returns)
        if not any(t in ret_type for t in _ITERATOR_TYPES):
            return ArtifactError(
                check="return_type",
                message=f"Method '{node.name}' returns '{ret_type}' but streaming methods must return Iterator/Generator",
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
        filename.endswith(ext) for ext in (".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".prisma")
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
