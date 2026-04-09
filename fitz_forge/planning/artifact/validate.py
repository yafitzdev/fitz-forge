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
    """Try parsing with recovery: raw -> dedent -> class wrap."""
    for attempt in [
        content,
        textwrap.dedent(content),
        "class _:\n    " + content.replace("\n", "\n    "),
    ]:
        try:
            return ast.parse(attempt)
        except SyntaxError:
            continue
    return None


def _check_parseable(content: str) -> ArtifactError | None:
    """Check if content is valid Python (with recovery)."""
    if _try_parse(content) is None:
        return ArtifactError(
            check="parseable",
            message="Content is not valid Python (even after quote fix/dedent/class wrap recovery)",
            suggestion="Ensure the output is syntactically valid Python code",
        )
    return None


def _check_empty(content: str) -> ArtifactError | None:
    """Check if content has actual code."""
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    code_lines = [line for line in lines if not line.startswith("#") and not line.startswith('"""')]
    if len(code_lines) < 2:
        return ArtifactError(
            check="empty",
            message="Content has no meaningful code (fewer than 2 non-comment lines)",
            suggestion="Write the actual implementation, not just comments or stubs",
        )
    has_def = any("def " in line for line in code_lines)
    if not has_def:
        return ArtifactError(
            check="empty",
            message="Content has no function or method definitions",
            suggestion="Include at least one function/method definition",
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

    violations = check_artifact({"filename": ctx.filename, "content": content}, lookup)

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


def validate(content: str, ctx: ArtifactContext) -> list[ArtifactError]:
    """Run all validation checks. Empty list = valid artifact.

    Checks are ordered by severity — parseable first (blocks everything),
    then structural checks, then semantic checks.
    """
    errors: list[ArtifactError] = []

    # Hard fails
    err = _check_parseable(content)
    if err:
        errors.append(err)
        return errors  # can't check anything else if unparseable

    err = _check_empty(content)
    if err:
        errors.append(err)
        return errors

    # Structural checks
    errors.extend(_check_fabrication(content, ctx))

    # Semantic checks (streaming-specific)
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
