# fitz_forge/planning/agent/_ts_compressor.py
"""Tree-sitter implementation of compress_python / _collapse_all_bodies.

Mirrors the ast version line-by-line. Called from compressor.py when
``grounding.index.get_engine() == "tree_sitter"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..validation.grounding._ts_inference import _unwrap_decorated
from ..validation.grounding._ts_parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node


_KEEP_BODY_LINES = 6
_MAX_INIT_ASSIGNMENTS = 25


def _is_string_doc_expr(node: "Node") -> bool:
    """True if ``node`` is an ``expression_statement`` wrapping a string literal.

    Mirrors ast's ``ast.Expr`` wrapping an ``ast.Constant`` whose value is
    a string — the docstring shape.
    """
    if node.type != "expression_statement":
        return False
    inner = next((c for c in node.children if c.is_named), None)
    return inner is not None and inner.type == "string"


def _is_ellipsis_expr(node: "Node") -> bool:
    """True if ``node`` is ``...`` (Ellipsis literal) as an expression statement."""
    if node.type != "expression_statement":
        return False
    inner = next((c for c in node.children if c.is_named), None)
    return inner is not None and inner.type == "ellipsis"


def _is_pass_stmt(node: "Node") -> bool:
    return node.type == "pass_statement"


def _iter_all_functions(root: "Node"):
    """Walk and yield every function_definition (sync/async, decorated)."""
    stack = [root]
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


def _iter_all_bodied_nodes(root: "Node"):
    """Yield module/class/function — anything that has a body block for doc stripping."""
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in ("module", "class_definition", "function_definition"):
            yield n
        elif n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type in ("class_definition", "function_definition"):
                yield inner
        stack.extend(n.children)


def _function_name(func_def: "Node") -> str | None:
    for c in func_def.children:
        if c.type == "identifier":
            return c.text.decode("utf-8")
    return None


def _body_statements(node: "Node") -> list["Node"]:
    """Return the named statement children of node's body.

    For ``module`` the body is the node itself; for class/function it's
    the ``block`` child.
    """
    if node.type == "module":
        return [c for c in node.children if c.is_named]
    body = next((c for c in node.children if c.type == "block"), None)
    if body is None:
        return []
    return [c for c in body.children if c.is_named]


def _keep_init_assignments(
    lines: list[str],
    body_start: int,
    body_end: int,
    replacements: dict[int, str],
    removals: list[tuple[int, int]],
) -> None:
    """Identical to compressor._keep_init_assignments — line-heuristic, no AST."""
    keep_lines: list[int] = []
    for ln in range(body_start, body_end + 1):
        if ln > len(lines):
            break
        line = lines[ln - 1]
        stripped = line.strip()
        if not stripped.startswith("self."):
            continue
        if "=" not in stripped:
            continue
        before_eq = stripped.split("=", 1)[0].strip()
        if before_eq.count(".") > 1:
            continue
        keep_lines.append(ln)

    if not keep_lines:
        first_line = lines[body_start - 1] if body_start <= len(lines) else ""
        indent = len(first_line) - len(first_line.lstrip())
        indent_str = first_line[:indent] if indent > 0 else "        "
        replacements[body_start] = f"{indent_str}...  # {body_end - body_start + 1} lines\n"
        removals.append((body_start + 1, body_end))
        return

    keep_set = set(keep_lines[:_MAX_INIT_ASSIGNMENTS])
    for ln in range(body_start, body_end + 1):
        if ln not in keep_set:
            removals.append((ln, ln))


def _strip_comments_and_blanks(source: str) -> str:
    """Copy of compressor._strip_comments_and_blanks — no AST needed."""
    out: list[str] = []
    prev_blank = False
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!") and "type:" not in stripped:
            continue
        if not stripped:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(line)
    return "".join(out)


def _lineno(node: "Node") -> int:
    """Convert tree-sitter start_point (0-indexed row) to ast-style lineno (1-indexed)."""
    return node.start_point[0] + 1


def _end_lineno(node: "Node") -> int:
    """Inclusive end line, 1-indexed (matches ast.end_lineno semantics)."""
    # tree-sitter end_point.column == 0 when node ends at newline; otherwise last char of row.
    end_row = node.end_point[0]
    end_col = node.end_point[1]
    if end_col == 0 and end_row > 0:
        end_row -= 1
    return end_row + 1


def compress_python(source: str) -> str:
    tree = parse_python(source)
    if tree is None:
        return source
    lines = source.splitlines(keepends=True)
    if not lines:
        return source

    removals: list[tuple[int, int]] = []
    replacements: dict[int, str] = {}

    for node in _iter_all_bodied_nodes(tree.root_node):
        stmts = _body_statements(node)

        # Strip leading docstring
        if stmts and _is_string_doc_expr(stmts[0]):
            removals.append((_lineno(stmts[0]), _end_lineno(stmts[0])))

        if node.type != "function_definition":
            continue

        body_start_idx = 1 if (stmts and _is_string_doc_expr(stmts[0])) else 0
        real_body = stmts[body_start_idx:]
        if not real_body:
            continue

        body_start = _lineno(real_body[0])
        body_end = _end_lineno(real_body[-1])
        body_lines = body_end - body_start + 1

        if body_lines <= _KEEP_BODY_LINES:
            continue

        # Skip trivial pass / ellipsis bodies
        if len(real_body) == 1 and (
            _is_pass_stmt(real_body[0]) or _is_ellipsis_expr(real_body[0])
        ):
            continue

        name = _function_name(node)
        if name in ("__init__", "_init_components", "setup", "_setup"):
            _keep_init_assignments(lines, body_start, body_end, replacements, removals)
            continue

        first_line = lines[body_start - 1] if body_start <= len(lines) else ""
        indent = len(first_line) - len(first_line.lstrip())
        indent_str = first_line[:indent] if indent > 0 else "        "
        replacements[body_start] = f"{indent_str}...  # {body_lines} lines\n"
        removals.append((body_start + 1, body_end))

    if not removals and not replacements:
        return _strip_comments_and_blanks(source)

    remove_lines: set[int] = set()
    for start, end in removals:
        for ln in range(start, end + 1):
            remove_lines.add(ln)

    result: list[str] = []
    for i, line in enumerate(lines, 1):
        if i in replacements:
            result.append(replacements[i])
        elif i not in remove_lines:
            result.append(line)
    return _strip_comments_and_blanks("".join(result))


def collapse_all_bodies(source: str) -> str:
    tree = parse_python(source)
    if tree is None:
        return source
    lines = source.splitlines(keepends=True)
    if not lines:
        return source

    removals: list[tuple[int, int]] = []
    replacements: dict[int, str] = {}

    for node in _iter_all_functions(tree.root_node):
        stmts = _body_statements(node)
        if not stmts:
            continue
        # Skip already-stubbed bodies
        if len(stmts) == 1 and (
            _is_pass_stmt(stmts[0]) or _is_ellipsis_expr(stmts[0])
        ):
            continue
        body_start_idx = 1 if _is_string_doc_expr(stmts[0]) else 0
        real_body = stmts[body_start_idx:]
        if not real_body:
            continue

        body_start = _lineno(real_body[0])
        body_end = _end_lineno(real_body[-1])
        first_line = lines[body_start - 1] if body_start <= len(lines) else ""
        indent = len(first_line) - len(first_line.lstrip())
        indent_str = first_line[:indent] if indent > 0 else "        "

        replacements[body_start] = f"{indent_str}...\n"
        if body_end > body_start:
            removals.append((body_start + 1, body_end))

    if not removals and not replacements:
        return source

    remove_lines: set[int] = set()
    for start, end in removals:
        for ln in range(start, end + 1):
            remove_lines.add(ln)

    result: list[str] = []
    for i, line in enumerate(lines, 1):
        if i in replacements:
            result.append(replacements[i])
        elif i not in remove_lines:
            result.append(line)
    return "".join(result)
