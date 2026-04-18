# fitz_forge/planning/artifact/validate.py
"""Output validation for generated artifacts.

Every check is deterministic — no LLM calls. Returns a list of
ArtifactError objects. Empty list = artifact is valid.

These are the SAME checks the V2 scorer runs, so if an artifact
passes validation here, the scorer will agree.
"""

from __future__ import annotations

import builtins
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

# Builtin names — populated once per process. Includes ``True``/``False``/``None``
# (identifier-shaped in tree-sitter for some shapes) plus all of builtins.
_BUILTINS: frozenset[str] = frozenset(builtins.__dict__.keys()) | frozenset(
    {"True", "False", "None", "Ellipsis", "NotImplemented", "__name__", "__file__",
     "__doc__", "__package__", "__loader__", "__spec__", "__builtins__", "__debug__"}
)
# `self` and `cls` are always implicit method receivers.
_IMPLICIT_BOUND: frozenset[str] = frozenset({"self", "cls"})

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


# ---------------------------------------------------------------------------
# Unbound-name (per-method scope resolution) — B11
# ---------------------------------------------------------------------------
#
# Invariant: every bare identifier read inside a function/method body must
# resolve to one of:
#   - a parameter of the (enclosing) method
#   - `self` / `cls`
#   - a local binding from assignment / for / with / except / walrus /
#     comprehension target inside the same function
#   - a sibling top-level def or class in the same artifact
#   - a module-level import (anywhere in the artifact, including inside
#     the surgical `class _:` recovery wrapper)
#   - a Python builtin
#
# This is per-artifact — no closure / sibling-artifact resolution. Catches
# the route shape where the body says ``request.X`` but the signature is
# flat positional params (NameError at runtime, invisible to per-artifact
# AST checks because the file parses fine).


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


def _close_name_matches(target: str, candidates: set[str], limit: int = 3) -> list[str]:
    """Top-`limit` closest candidate name matches by edit distance.

    Mirrors closure.py:_close_method_matches but operates on a flat name
    set (not method names on a specific owner). Used for the
    "did you mean one of: …" suggestion text on unbound-name violations.
    """
    if not target:
        return []
    cands = [c for c in candidates if c and c != target]
    if not cands:
        return []
    scored: list[tuple[int, str]] = []
    for c in cands:
        d = _levenshtein(target, c)
        prefix = 0
        for x, y in zip(target, c, strict=False):
            if x == y:
                prefix += 1
            else:
                break
        scored.append((d - prefix, c))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [name for _, name in scored[:limit]]


def _walk_skip_nested(root: "Node"):
    """Yield every descendant of `root` excluding nested function/lambda
    bodies. Stops at function_definition / lambda / decorated_definition
    boundaries — their contents are a separate scope."""
    stack: list[Node] = list(root.children)
    while stack:
        n = stack.pop()
        if n.type in ("function_definition", "lambda"):
            continue
        if n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type == "function_definition":
                continue
        yield n
        stack.extend(n.children)


def _collect_pattern_targets(node: "Node", out: set[str]) -> None:
    """Collect all binding identifiers from an assignment / for / pattern target.

    Handles: bare identifier, pattern_list (tuple unpack), list_splat_pattern
    (`*x`), tuple_pattern, list_pattern, parenthesized patterns. Skips
    `subscript` (`a[0] = ...` doesn't bind `a`) and `attribute`
    (`obj.x = ...` doesn't bind `obj`).
    """
    if node.type == "identifier":
        out.add(node.text.decode("utf-8"))
        return
    if node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        for c in node.children:
            if c.is_named:
                _collect_pattern_targets(c, out)
        return
    if node.type == "list_splat_pattern":
        for c in node.children:
            if c.type == "identifier":
                out.add(c.text.decode("utf-8"))
        return
    # parenthesized_expression / tuple wrapping — descend
    for c in node.children:
        if c.is_named and c.type not in ("subscript", "attribute"):
            _collect_pattern_targets(c, out)


def _collect_param_names(func_def: "Node", out: set[str]) -> None:
    """Add parameter names from a function_definition / lambda to `out`.

    Strips ``*`` / ``**`` prefixes — we want the bare name as it appears
    in the body. Handles typed_parameter / default_parameter /
    typed_default_parameter / list_splat_pattern / dictionary_splat_pattern.
    """
    params_node = next(
        (c for c in func_def.children if c.type in ("parameters", "lambda_parameters")),
        None,
    )
    if params_node is None:
        return
    for p in params_node.children:
        if p.type == "identifier":
            out.add(p.text.decode("utf-8"))
        elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                out.add(ident.text.decode("utf-8"))
        elif p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                out.add(ident.text.decode("utf-8"))


def _collect_module_imports(root: "Node", out: set[str]) -> None:
    """Collect names imported at module level into `out`.

    Walks the whole tree (skipping nested defs/lambdas) so imports inside
    the surgical ``class _:`` recovery wrapper are still visible. For
    ``from M import X`` adds ``X``; for ``import M.N`` adds ``M`` (the
    leftmost dotted name); for ``import M as A`` / ``from M import X as Y``
    adds the alias.
    """
    stack: list[Node] = [root]
    while stack:
        n = stack.pop()
        # Skip nested function/lambda contents — they have their own scope.
        if n.type in ("function_definition", "lambda"):
            continue
        if n.type == "import_statement":
            for c in n.children:
                if c.type == "dotted_name":
                    # Leftmost identifier is the bound name (`os` in `os.path`).
                    first = next((x for x in c.children if x.type == "identifier"), None)
                    if first is not None:
                        out.add(first.text.decode("utf-8"))
                elif c.type == "aliased_import":
                    alias = next(
                        (x for x in reversed(c.children) if x.type == "identifier"),
                        None,
                    )
                    if alias is not None:
                        out.add(alias.text.decode("utf-8"))
        elif n.type == "import_from_statement":
            # Skip the module specifier (first dotted_name / relative_import
            # after `from`); collect names after `import`.
            seen_import_kw = False
            for c in n.children:
                if c.type == "import":
                    seen_import_kw = True
                    continue
                if not seen_import_kw:
                    continue
                if c.type == "dotted_name":
                    first = next((x for x in c.children if x.type == "identifier"), None)
                    if first is not None:
                        out.add(first.text.decode("utf-8"))
                elif c.type == "aliased_import":
                    alias = next(
                        (x for x in reversed(c.children) if x.type == "identifier"),
                        None,
                    )
                    if alias is not None:
                        out.add(alias.text.decode("utf-8"))
                elif c.type == "wildcard_import":
                    # `from M import *` — we can't know what it brings in,
                    # so suppress unbound-name checking for the artifact.
                    out.add("__star_import__")
            continue
        stack.extend(n.children)


def _collect_module_top_names(root: "Node", out: set[str]) -> None:
    """Collect names of module-level def/class statements (sibling exports).

    Walks all children of root AND descends into the surgical ``class _:``
    wrapper (its body contents are conceptually module-level when the
    artifact lacks an outer class). For class_definition / function_definition,
    record the identifier.
    """
    stack: list[Node] = [root]
    seen_root = False
    while stack:
        n = stack.pop()
        if n is root:
            seen_root = True
            stack.extend(n.children)
            continue
        if n.type in ("function_definition", "class_definition"):
            ident = next((c for c in n.children if c.type == "identifier"), None)
            if ident is not None:
                out.add(ident.text.decode("utf-8"))
            # Don't descend — methods live inside but aren't sibling exports.
            continue
        if n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type in ("function_definition", "class_definition"):
                ident = next((c for c in inner.children if c.type == "identifier"), None)
                if ident is not None:
                    out.add(ident.text.decode("utf-8"))
            continue
        # Descend through the synthetic `class _:` wrapper body so siblings
        # inside it count as module-level for the artifact.
        if n.type == "block" and seen_root:
            stack.extend(n.children)


def _collect_module_assignments(root: "Node", out: set[str]) -> None:
    """Collect names bound by module-level assignments (constants, etc.).

    Same scope policy as _collect_module_top_names — descends into the
    synthetic ``class _:`` wrapper body but stops at any nested
    function/class.
    """
    stack: list[tuple[Node, bool]] = [(root, True)]
    while stack:
        n, is_module_scope = stack.pop()
        if n.type in ("function_definition", "lambda"):
            continue
        if n.type == "class_definition":
            # Descend ONLY for the synthetic `_` wrapper to reach its body
            # (its assignments are module-level for the artifact).
            ident = next((c for c in n.children if c.type == "identifier"), None)
            if ident is not None and ident.text.decode("utf-8") == "_":
                for c in n.children:
                    if c.type == "block":
                        for sub in c.children:
                            stack.append((sub, True))
            continue
        if n.type == "expression_statement" and is_module_scope:
            for c in n.children:
                if c.type == "assignment":
                    target = next(
                        (x for x in c.children if x.is_named and x.type != "type"),
                        None,
                    )
                    if target is not None:
                        _collect_pattern_targets(target, out)
            continue
        for c in n.children:
            stack.append((c, is_module_scope))


def _collect_local_bindings(func_def: "Node", out: set[str]) -> None:
    """Collect every name bound inside a function body.

    Sources: assignment LHS, augmented_assignment LHS, for/for_in_clause
    targets, with_statement `as` targets, except_clause `as` targets,
    walrus (named_expression) targets, nonlocal/global declarations, and
    nested def/class names (``def inner`` introduces ``inner`` into the
    enclosing scope). Stops descending INTO nested function/lambda
    bodies — their own params/locals belong to their own scope — but
    still records nested def/class NAMES at this scope.
    """
    body = next((c for c in func_def.children if c.type == "block"), None)
    if body is None:
        return
    # Custom traversal: yield nested def/class node themselves (so we can
    # record the name) but never descend into their bodies.
    stack: list[Node] = list(body.children)
    while stack:
        n = stack.pop()
        # Record nested def/class name but don't descend into its body.
        if n.type in ("function_definition", "class_definition"):
            ident = next((c for c in n.children if c.type == "identifier"), None)
            if ident is not None:
                out.add(ident.text.decode("utf-8"))
            continue
        if n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type in ("function_definition", "class_definition"):
                ident = next((c for c in inner.children if c.type == "identifier"), None)
                if ident is not None:
                    out.add(ident.text.decode("utf-8"))
            continue
        if n.type == "lambda":
            # Lambda body isn't a binding source for the enclosing scope;
            # don't descend.
            continue
        if n.type in ("assignment", "augmented_assignment"):
            target = next(
                (c for c in n.children if c.is_named and c.type != "type"),
                None,
            )
            if target is not None:
                _collect_pattern_targets(target, out)
        elif n.type == "named_expression":
            target = next((c for c in n.children if c.type == "identifier"), None)
            if target is not None:
                out.add(target.text.decode("utf-8"))
        elif n.type in ("for_statement", "for_in_clause"):
            # `for <target> in <iter>:` — target sits before the `in` keyword.
            for c in n.children:
                if not c.is_named:
                    if c.type == "in":
                        break
                    continue
                _collect_pattern_targets(c, out)
                # If we've consumed the target (single named pattern), keep
                # going — multiple bindings via pattern_list are handled
                # inside _collect_pattern_targets in one call.
        elif n.type == "as_pattern":
            # Used by both `with ... as X` and `except E as X`. The
            # binding sits in `as_pattern_target`.
            for c in n.children:
                if c.type == "as_pattern_target":
                    for sub in c.children:
                        if sub.type == "identifier":
                            out.add(sub.text.decode("utf-8"))
        elif n.type in ("global_statement", "nonlocal_statement"):
            for c in n.children:
                if c.type == "identifier":
                    out.add(c.text.decode("utf-8"))
        # Descend into children — but NOT into nested function/class/lambda
        # (handled above with `continue`). Bindings can appear at any depth
        # (e.g. inside `if` blocks, `try` blocks, comprehensions).
        stack.extend(n.children)


def _same_node(a: "Node", b: "Node") -> bool:
    """Identity check that survives tree-sitter's per-access wrapper churn.

    Tree-sitter's Python bindings instantiate a new wrapper object on each
    `.children` / `.parent` access, so `is` comparisons fail even for the
    same underlying node. Compare by byte range — unique within a parse.
    """
    return a.start_byte == b.start_byte and a.end_byte == b.end_byte and a.type == b.type


def _is_load_position(ident: "Node") -> bool:
    """True iff the identifier node is a name READ (not a binding target).

    Filters out:
      - keyword_argument's leftmost identifier (the key, e.g. `name=value`
        — `name` is the param name, not a reference)
      - identifiers inside `import` / `import_from` / `aliased_import` /
        `dotted_name` parts of imports / `wildcard_import`
      - identifiers that are the function/class identifier of a definition
      - assignment LHS / for-target / pattern targets
      - the keyword half of named_expression (walrus LHS)
      - global/nonlocal declarations
      - attribute's right-hand identifier (`.attr` part — handled separately)
      - dotted name pieces past the leftmost (`os.path.join` — only `os` loads)
      - identifiers inside `as_pattern_target`
      - identifiers in parameter declarations
      - `type` nodes (annotations) — skipped by the caller, defensive here
    """
    p = ident.parent
    if p is None:
        return False

    # Direct parent screening
    pt = p.type

    # function/class identifier — `def foo(...)` makes `foo` the name node
    if pt in ("function_definition", "class_definition", "lambda"):
        # the identifier child of these IS the def name, not a load
        return False

    # imports
    if pt in (
        "import_statement",
        "import_from_statement",
        "aliased_import",
        "dotted_name",
        "wildcard_import",
        "relative_import",
        "import_prefix",
    ):
        return False

    # parameter forms
    if pt in (
        "parameters",
        "lambda_parameters",
        "typed_parameter",
        "default_parameter",
        "typed_default_parameter",
        "list_splat_pattern",
        "dictionary_splat_pattern",
    ):
        # exception: a default_parameter's RHS value is a load — but the
        # value sits as a non-identifier child in most cases. The
        # identifier child is the param name. Conservative: treat as
        # binding/non-load.
        if pt == "default_parameter":
            # `c=expr` — children: identifier, '=', expr. Only the
            # identifier is the param name; if the value is itself a bare
            # identifier we still want to flag it as a load.
            children = [c for c in p.children if c.is_named]
            # The first named child is the param name; later named
            # children are part of the default value.
            if children and _same_node(ident, children[0]):
                return False
            return True
        if pt == "typed_default_parameter":
            children = [c for c in p.children if c.is_named]
            if children and _same_node(ident, children[0]):
                return False
            return True
        return False

    # assignment LHS — only the FIRST named child of `assignment` /
    # `augmented_assignment` is the target; subsequent named children
    # (the RHS expression) are loads.
    if pt in ("assignment", "augmented_assignment"):
        first_named = next((c for c in p.children if c.is_named and c.type != "type"), None)
        if first_named is not None and _same_node(ident, first_named):
            return False
        return True

    # walrus
    if pt == "named_expression":
        first_named = next((c for c in p.children if c.is_named), None)
        if first_named is not None and _same_node(ident, first_named):
            return False
        return True

    # for-statement / for-in-clause target sits before the `in` keyword
    if pt in ("for_statement", "for_in_clause"):
        # Target identifier(s) come before the `in` keyword child.
        for c in p.children:
            if not c.is_named:
                if c.type == "in":
                    # past the keyword — `ident` after `in` is the iter source (load)
                    return True
                continue
            if _same_node(c, ident):
                return False
        return True

    # pattern container types — bindings, not loads
    if pt in (
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "as_pattern_target",
    ):
        return False

    # global / nonlocal declarations don't load
    if pt in ("global_statement", "nonlocal_statement"):
        return False

    # keyword_argument: `name=value` — leftmost identifier is the key
    if pt == "keyword_argument":
        first_named = next((c for c in p.children if c.is_named), None)
        if first_named is not None and _same_node(ident, first_named):
            return False
        return True

    # attribute access: only the LEFTMOST identifier of a `obj.attr` is a
    # load; the right-hand `.attr` is field access (separate concern).
    if pt == "attribute":
        # children shape: <left>, '.', identifier
        named = [c for c in p.children if c.is_named]
        if named and _same_node(ident, named[0]):
            return True
        # ident is the .attr piece — not a load of a name
        return False

    # `type` nodes appear in annotations — those identifiers ARE loads
    # (the annotation must reference an in-scope name like `int`/`str`/etc).
    # Just fall through.

    # everything else (call args, return, binary_operator, etc.) is a load.
    return True


def _enclosing_function_scope_locals(
    func_def: "Node", module_imports: set[str], module_top: set[str],
    module_assignments: set[str],
) -> set[str]:
    """Build the bound-name set for `func_def` (a function_definition node).

    Union of: params, implicit `self`/`cls`, local bindings (including
    nested-def names hoisted into this scope), module imports, module-top
    sibling defs/classes, module-level assignments, builtins.
    """
    bound: set[str] = set(_IMPLICIT_BOUND)
    _collect_param_names(func_def, bound)
    _collect_local_bindings(func_def, bound)
    bound |= module_imports
    bound |= module_top
    bound |= module_assignments
    bound |= _BUILTINS
    return bound


def _iter_load_identifiers(func_def: "Node"):
    """Yield identifier nodes inside `func_def`'s body OR default-param
    expressions that read a name.

    Skips identifiers in nested function/lambda bodies — those have
    their own scope and are walked separately when this function is
    called for each function in the artifact.

    Default-param values (``def f(x=DEFAULT)``) execute in the enclosing
    scope at def-time, so any free name in the default expression must
    resolve like any other body load.
    """
    # Body
    body = next((c for c in func_def.children if c.type == "block"), None)
    if body is not None:
        for n in _walk_skip_nested(body):
            if n.type != "identifier":
                continue
            if _is_load_position(n):
                yield n

    # Default param values — walk inside default_parameter / typed_default_parameter,
    # skipping the parameter name itself (the first named child).
    params_node = next(
        (c for c in func_def.children if c.type in ("parameters", "lambda_parameters")),
        None,
    )
    if params_node is None:
        return
    for p in params_node.children:
        if p.type not in ("default_parameter", "typed_default_parameter"):
            continue
        named = [c for c in p.children if c.is_named]
        # The first named child is the param name; subsequent named children
        # are the type annotation (typed_default_parameter only) and the
        # default value expression. We want loads inside the value expression.
        for sub in named[1:]:
            if sub.type == "type":
                # Types are annotations — their identifiers are loads but
                # let's keep that consistent with body loads. Walk it.
                for x in _walk_skip_nested(sub):
                    if x.type == "identifier" and _is_load_position(x):
                        yield x
                continue
            # The value expression
            if sub.type == "identifier":
                if _is_load_position(sub):
                    yield sub
            else:
                for x in _walk_skip_nested(sub):
                    if x.type == "identifier" and _is_load_position(x):
                        yield x


def _all_artifact_functions(root: "Node"):
    """Yield every function_definition in the artifact tree."""
    yield from _iter_all_functions(root)


def _check_unbound_names(content: str, ctx: ArtifactContext) -> list[ArtifactError]:
    """Per-method scope-resolution check (B11).

    For each function in the artifact, collect bound names (params,
    locals, imports, sibling defs, builtins) and flag any identifier
    read that doesn't resolve. False positives are suppressed via the
    bound-set construction (`self`/`cls`, implicit; star-imports
    suppress the whole artifact; default param values flag through
    properly because their RHS sits in a load position).
    """
    tree = parse_python(content)
    if tree is None:
        return []  # parseable check handles this

    root = tree.root_node

    module_imports: set[str] = set()
    _collect_module_imports(root, module_imports)
    if "__star_import__" in module_imports:
        # `from M import *` — we can't enumerate brought-in names, so
        # skip the whole check rather than emit false positives.
        return []

    module_top: set[str] = set()
    _collect_module_top_names(root, module_top)

    module_assignments: set[str] = set()
    _collect_module_assignments(root, module_assignments)

    errors: list[ArtifactError] = []

    for fn in _all_artifact_functions(root):
        bound = _enclosing_function_scope_locals(
            fn, module_imports, module_top, module_assignments
        )
        # Method-local: don't suggest builtins (huge / noisy). Suggestions
        # come from the function's own params + local bindings only.
        suggest_pool: set[str] = set()
        _collect_param_names(fn, suggest_pool)
        _collect_local_bindings(fn, suggest_pool)

        seen_unbound: set[str] = set()
        for ident in _iter_load_identifiers(fn):
            name = ident.text.decode("utf-8")
            if name in bound:
                continue
            if name in seen_unbound:
                continue
            seen_unbound.add(name)
            line = ident.start_point[0] + 1
            close = _close_name_matches(name, suggest_pool)
            if close:
                hint = f" Did you mean one of: {', '.join(close)}?"
            else:
                hint = ""
            fn_name_node = next((c for c in fn.children if c.type == "identifier"), None)
            fn_name = fn_name_node.text.decode("utf-8") if fn_name_node else "<anonymous>"
            errors.append(
                ArtifactError(
                    check="unbound_name",
                    message=(
                        f"unbound_name: '{name}' referenced at line {line} in "
                        f"{fn_name}() but not in scope (no matching parameter, "
                        f"local, import, or sibling def)."
                    ),
                    suggestion=(
                        f"Bind '{name}' before use, add it as a parameter, or "
                        f"correct the name.{hint}"
                    ),
                )
            )

    return errors


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
        # Per-method scope-resolution (B11): every name read in a body must
        # bind to a param / local / import / sibling-def / builtin.
        errors.extend(_check_unbound_names(content, ctx))

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
