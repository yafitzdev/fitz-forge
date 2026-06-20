# fitz_forge/planning/agent/indexer.py
"""
Structural index builder for codebase context gathering.

Extracts structural information (classes, functions, imports) from source
files using language-appropriate methods:
  - Python: tree-sitter (classes with bases, functions with signatures, imports)
  - Config (YAML/JSON/TOML): safe parsers for top-level keys
  - Markdown/RST: regex heading extraction
  - Generic code (JS/TS/Go/Rust/Java/C/C++/Ruby): regex patterns
  - Fallback: no structural info line

The index gives the LLM architectural visibility into the entire codebase
without reading full file contents, breaking the circular retrieval problem.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ..validation.grounding.inference import (
    _class_body,
    _function_is_async,
    _function_name,
    _returns_annotation,
    _rightmost_attribute_name,
    _unwrap_decorated,
    iter_class_methods,
    unparse_annotation,
)
from ..validation.grounding.parser import parse_python

if TYPE_CHECKING:
    from tree_sitter import Node

logger = logging.getLogger(__name__)

# Maximum total index size in characters before truncation.
# At ~4 chars/token this is ~30K tokens — fits comfortably in 32K+ context
# windows while leaving room for the rest of the prompt.
_MAX_INDEX_CHARS = 120_000

# Extension → extractor mapping.
# INDEXABLE_EXTENSIONS is the union — used by the tree walker to skip files
# that the indexer can't extract anything useful from.
_PYTHON_EXTS = {".py"}
_CONFIG_EXTS = {".yaml", ".yml", ".json", ".toml"}
_MARKDOWN_EXTS = {".md", ".rst"}
_GENERIC_CODE_EXTS = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".scala",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".rb",
    ".cs",
    ".swift",
    ".php",
    ".lua",
    ".zig",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".hs",
    ".ml",
    ".mli",
    ".sh",
    ".bash",
    ".zsh",
}

# All extensions the indexer can extract structure from.
# Files without these extensions are invisible to the agent.
INDEXABLE_EXTENSIONS = _PYTHON_EXTS | _CONFIG_EXTS | _MARKDOWN_EXTS | _GENERIC_CODE_EXTS

# Decorators worth showing in the structural index (architectural cues).
_KEY_DECORATORS = frozenset(
    {
        "dataclass",
        "abstractmethod",
        "property",
        "staticmethod",
        "classmethod",
        "override",
    }
)


def build_structural_index(
    source_dir: str,
    file_list: list[str],
    max_file_bytes: int = 50_000,
    connection_counts: dict[str, int] | None = None,
    max_chars: int = _MAX_INDEX_CHARS,
) -> str:
    """
    Build a compact structural index of all files in the codebase.

    Args:
        source_dir: Absolute path to source directory root.
        file_list: List of relative file paths (posix-style) to index.
        max_file_bytes: Maximum bytes to read per file.
        connection_counts: Optional mapping of file path to import
            connection count. Used to prioritize truncation — files
            with fewer connections lose detail first.

    Returns:
        Multi-line text index with structural info per file.
    """
    root = Path(source_dir).resolve()
    entries: list[tuple[str, str]] = []  # (rel_path, index_text)

    for rel_path in file_list:
        full_path = root / rel_path
        if not full_path.is_file():
            continue

        try:
            raw = full_path.read_bytes()[:max_file_bytes]
            content = raw.decode("utf-8", errors="replace")
        except OSError:
            continue

        if not content.strip():
            continue

        suffix = PurePosixPath(rel_path).suffix.lower()
        info = _extract_structure(suffix, content, rel_path)
        if info:
            entries.append((rel_path, info))
        else:
            entries.append((rel_path, "(no structural info)"))

    # Format and apply size budget
    return _format_index(entries, connection_counts, max_chars=max_chars)


def _extract_structure(suffix: str, content: str, rel_path: str) -> str:
    """Dispatch to the appropriate extractor based on file extension."""
    if suffix in _PYTHON_EXTS:
        return _extract_python(content)
    if suffix in _CONFIG_EXTS:
        return _extract_config(suffix, content)
    if suffix in _MARKDOWN_EXTS:
        return _extract_markdown(content)
    if suffix in _GENERIC_CODE_EXTS:
        return _extract_generic_code(content)
    return ""


def _node_name(node: Node | None) -> str:
    """Human-readable name from a tree-sitter node (ast.unparse analogue)."""
    if node is None:
        return ""
    if node.type == "identifier":
        return node.text.decode("utf-8")
    if node.type == "attribute":
        # Recurse into value, then append .attr
        value = next((c for c in node.children if c.type in ("identifier", "attribute")), None)
        idents = [c for c in node.children if c.type == "identifier"]
        if value is None or value.type == "identifier":
            if idents:
                return ".".join(i.text.decode("utf-8") for i in idents)
            return "?"
        left = _node_name(value)
        attr_ident = idents[-1] if idents else None
        if attr_ident is not None:
            return (
                f"{left}.{attr_ident.text.decode('utf-8')}"
                if left
                else attr_ident.text.decode("utf-8")
            )
        return left or "?"
    if node.type == "subscript":
        value = next((c for c in node.children if c.is_named), None)
        return _node_name(value)
    return "?"


def _extract_key_decorators(class_or_func_node: Node) -> list[str]:
    """Extract recognised decorator names from a decorated_definition wrapper."""
    result: list[str] = []
    parent = class_or_func_node.parent
    if parent is None or parent.type != "decorated_definition":
        return result
    for c in parent.children:
        if c.type != "decorator":
            continue
        body = next((x for x in c.children if x.is_named), None)
        name = None
        if body is None:
            continue
        if body.type == "identifier":
            name = body.text.decode("utf-8")
        elif body.type == "attribute":
            name = _rightmost_attribute_name(body)
        elif body.type == "call":
            callee = next(
                (x for x in body.children if x.is_named and x.type != "argument_list"),
                None,
            )
            if callee is None:
                continue
            if callee.type == "identifier":
                name = callee.text.decode("utf-8")
            elif callee.type == "attribute":
                name = _rightmost_attribute_name(callee)
        if name and name in _KEY_DECORATORS:
            result.append(name)
    return result


def _iter_top_level(root: Node):
    """Yield each top-level definition, unwrapping decorated wrappers."""
    for c in root.children:
        if c.type == "decorated_definition":
            inner = _unwrap_decorated(c)
            if inner.type in ("function_definition", "class_definition"):
                yield inner
        else:
            yield c


def _all_param_names_except_self(func_def: Node) -> list[str]:
    """Positional-or-keyword params only, excluding self."""
    params_node = next((c for c in func_def.children if c.type == "parameters"), None)
    if params_node is None:
        return []
    out: list[str] = []
    for p in params_node.children:
        if p.type in ("keyword_separator", "list_splat_pattern", "dictionary_splat_pattern"):
            break
        name: str | None = None
        if p.type == "identifier":
            name = p.text.decode("utf-8")
        elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                name = ident.text.decode("utf-8")
        if name and name != "self":
            out.append(name)
    return out


def _formatted_param(p: Node) -> str | None:
    """Return formatted ``name[: Ann]`` string for a parameter node, or None to skip."""
    if p.type == "identifier":
        name = p.text.decode("utf-8")
        return None if name == "self" else name
    if p.type == "typed_parameter":
        ident = next((c for c in p.children if c.type == "identifier"), None)
        tnode = next((c for c in p.children if c.type == "type"), None)
        if ident is None:
            return None
        name = ident.text.decode("utf-8")
        if name == "self":
            return None
        if tnode is None:
            return name
        unparsed = unparse_annotation(tnode) or "?"
        return f"{name}: {unparsed}"
    if p.type == "default_parameter":
        ident = next((c for c in p.children if c.type == "identifier"), None)
        if ident is None:
            return None
        return ident.text.decode("utf-8")
    if p.type == "typed_default_parameter":
        ident = next((c for c in p.children if c.type == "identifier"), None)
        tnode = next((c for c in p.children if c.type == "type"), None)
        if ident is None:
            return None
        name = ident.text.decode("utf-8")
        if tnode is None:
            return name
        return f"{name}: {unparse_annotation(tnode) or '?'}"
    return None


def _module_docstring(root: Node) -> str | None:
    """First real statement's string literal, if any."""
    for c in root.children:
        if not c.is_named or c.type == "comment":
            continue
        if c.type == "expression_statement":
            inner = next((x for x in c.children if x.is_named), None)
            if inner is not None and inner.type == "string":
                text = inner.text.decode("utf-8").strip()
                for q in ('"""', "'''", '"', "'"):
                    if text.startswith(q) and text.endswith(q) and len(text) >= 2 * len(q):
                        text = text[len(q) : -len(q)]
                        break
                first_line = text.strip().splitlines()[0] if text.strip() else ""
                return first_line
        return None
    return None


def _extract_python(content: str) -> str:
    """Extract structure from Python files using tree-sitter.

    Falls back to regex if parsing fails (syntax errors).
    """
    tree = parse_python(content)
    if tree is None:
        return _extract_python_regex(content)
    root = tree.root_node
    lines: list[str] = []

    doc = _module_docstring(root)
    if doc:
        lines.append(f'doc: "{doc}"')

    classes_out: list[str] = []
    for node in _iter_top_level(root):
        if node.type != "class_definition":
            continue
        cname_node = next((c for c in node.children if c.type == "identifier"), None)
        if cname_node is None:
            continue
        bases: list[str] = []
        args = next((c for c in node.children if c.type == "argument_list"), None)
        if args is not None:
            for c in args.children:
                if c.is_named:
                    bases.append(_node_name(c))
        methods: list[str] = []
        fields: list[str] = []
        for m in iter_class_methods(node):
            mname = _function_name(m)
            if mname is None:
                continue
            m_str = mname
            ret_node = _returns_annotation(m)
            if ret_node is not None:
                ret = unparse_annotation(ret_node)
                if ret:
                    m_str += f" -> {ret}"
            methods.append(m_str)
        # Class-level annotated assignments (fields)
        body = _class_body(node)
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
                    fields.append(target.text.decode("utf-8"))

        cls_str = cname_node.text.decode("utf-8")
        if bases:
            cls_str += f"({', '.join(bases)})"
        decs = _extract_key_decorators(node)
        if decs:
            cls_str += f" [{', '.join(f'@{d}' for d in decs)}]"
        if methods:
            cls_str += f" [{', '.join(methods)}]"
        elif fields:
            cls_str += f" [{', '.join(fields)}]"
        classes_out.append(cls_str)
    if classes_out:
        lines.append(f"classes: {'; '.join(classes_out)}")

    # Top-level functions
    functions_out: list[str] = []
    for node in _iter_top_level(root):
        if node.type != "function_definition":
            continue
        fname = _function_name(node)
        if fname is None:
            continue
        params = _all_param_names_except_self(node)
        func_str = f"{fname}({', '.join(params)})"
        ret_node = _returns_annotation(node)
        if ret_node is not None:
            ret = unparse_annotation(ret_node)
            if ret:
                func_str += f" -> {ret}"
        decs = _extract_key_decorators(node)
        if decs:
            func_str += f" [{', '.join(f'@{d}' for d in decs)}]"
        functions_out.append(func_str)
    if functions_out:
        lines.append(f"functions: {', '.join(functions_out)}")

    # Imports — full tree walk
    imports: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "import_statement":
            for c in n.children:
                if c.type == "dotted_name":
                    imports.add(c.text.decode("utf-8"))
                elif c.type == "aliased_import":
                    orig = next((x for x in c.children if x.type == "dotted_name"), None)
                    if orig is not None:
                        imports.add(orig.text.decode("utf-8"))
        elif n.type == "import_from_statement":
            module_node = next(
                (c for c in n.children if c.type in ("dotted_name", "relative_import")),
                None,
            )
            if module_node is not None:
                if module_node.type == "relative_import":
                    inner = next(
                        (c for c in module_node.children if c.type == "dotted_name"),
                        None,
                    )
                    text = inner.text.decode("utf-8") if inner else ""
                else:
                    text = module_node.text.decode("utf-8")
                if text:
                    imports.add(text)
        elif n.type == "future_import_statement":
            imports.add("__future__")
        stack.extend(n.children)
    if imports:
        lines.append(f"imports: {', '.join(sorted(imports))}")

    # __all__ exports
    for node in _iter_top_level(root):
        if node.type != "expression_statement":
            continue
        inner = next((c for c in node.children if c.is_named), None)
        if inner is None or inner.type != "assignment":
            continue
        target = next((c for c in inner.children if c.is_named), None)
        if target is None or target.type != "identifier":
            continue
        if target.text.decode("utf-8") != "__all__":
            continue
        saw_eq = False
        value = None
        for c in inner.children:
            if not c.is_named and c.type == "=":
                saw_eq = True
                continue
            if saw_eq and c.is_named:
                value = c
                break
        if value is None or value.type not in ("list", "tuple"):
            continue
        names: list[str] = []
        for elt in value.children:
            if elt.type == "string":
                raw = elt.text.decode("utf-8")
                for q in ('"""', "'''", '"', "'"):
                    if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                        names.append(raw[len(q) : -len(q)])
                        break
        if names:
            lines.append(f"exports: {', '.join(names)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Method flow extraction — internal pipeline of complex methods
# ---------------------------------------------------------------------------

_FLOW_SKIP_METHODS = frozenset(
    {
        "info",
        "debug",
        "warning",
        "error",
        "exception",
        "get",
        "set",
        "append",
        "extend",
        "update",
        "pop",
        "items",
        "keys",
        "values",
        "strip",
        "lower",
        "upper",
        "replace",
        "split",
        "join",
        "format",
        "encode",
        "decode",
        "result",
        "submit",
        "add",
        "remove",
        "clear",
        "copy",
        "startswith",
        "endswith",
        "isinstance",
        "issubclass",
    }
)

_FLOW_SKIP_OBJECTS = frozenset(
    {
        "logger",
        "log",
        "timings",
        "time",
        "uuid",
        "re",
        "os",
        "sys",
        "json",
        "pool",
        "math",
        "hashlib",
        "threading",
    }
)

# Minimum method body lines to extract flow (short methods aren't pipelines)
_MIN_METHOD_LINES = 30


def extract_method_flows(content: str, min_lines: int = _MIN_METHOD_LINES) -> str:
    """Extract internal pipeline flow of complex methods via tree-sitter.

    For each method longer than min_lines, resolves self._component.method()
    calls to their component types (from __init__ assignments) and produces
    a compact step list showing the method's internal pipeline.

    Returns a multi-line string with one section per complex method, or
    empty string if no complex methods found.
    """
    tree = parse_python(content)
    if tree is None:
        return ""
    root = tree.root_node
    results: list[str] = []

    for cls in _iter_top_level(root):
        if cls.type != "class_definition":
            continue
        cname_node = next((c for c in cls.children if c.type == "identifier"), None)
        cname = cname_node.text.decode("utf-8") if cname_node else "?"

        # component_types from __init__ assignments
        component_types: dict[str, str] = {}
        for method in iter_class_methods(cls):
            if _function_name(method) != "__init__":
                continue
            body = next((c for c in method.children if c.type == "block"), None)
            if body is None:
                continue
            stack = list(body.children)
            while stack:
                n = stack.pop()
                if n.type == "assignment":
                    target = next((c for c in n.children if c.is_named), None)
                    if target is None or target.type != "attribute":
                        stack.extend(n.children)
                        continue
                    idents = [c for c in target.children if c.type == "identifier"]
                    if (
                        len(idents) == 2
                        and idents[0].text.decode("utf-8") == "self"
                        and idents[1].text.decode("utf-8").startswith("_")
                    ):
                        saw_eq = False
                        value = None
                        for c in n.children:
                            if not c.is_named and c.type == "=":
                                saw_eq = True
                                continue
                            if saw_eq and c.is_named:
                                value = c
                                break
                        if value is not None and value.type == "call":
                            callee = next(
                                (
                                    c
                                    for c in value.children
                                    if c.is_named and c.type != "argument_list"
                                ),
                                None,
                            )
                            if callee is not None:
                                if callee.type == "identifier":
                                    component_types[idents[1].text.decode("utf-8")] = (
                                        callee.text.decode("utf-8")
                                    )
                                elif callee.type == "attribute":
                                    name = _rightmost_attribute_name(callee)
                                    if name:
                                        component_types[idents[1].text.decode("utf-8")] = name
                stack.extend(n.children)

        # Flow steps per complex method
        for method in iter_class_methods(cls):
            mname = _function_name(method)
            if mname is None or mname.startswith("__"):
                continue
            body_lines = method.end_point[0] - method.start_point[0]
            if body_lines < min_lines:
                continue
            steps = _extract_flow_steps(method, component_types)
            if len(steps) < 3:
                continue
            results.append(f"flow {cname}.{mname}(): " + " → ".join(steps))

    return "\n".join(results)


def _extract_flow_steps(
    method: Node,
    component_types: dict[str, str],
) -> list[str]:
    """Extract ordered pipeline steps from a method body."""
    calls: list[tuple[int, str]] = []

    body = next((c for c in method.children if c.type == "block"), None)
    if body is None:
        return []
    stack = list(body.children)
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type != "call":
            continue
        line = n.start_point[0] + 1
        callee = next(
            (c for c in n.children if c.is_named and c.type != "argument_list"),
            None,
        )
        if callee is None:
            continue
        if callee.type == "attribute":
            idents = [c for c in callee.children if c.type == "identifier"]
            attr = idents[-1].text.decode("utf-8") if idents else ""
            if attr in _FLOW_SKIP_METHODS:
                continue
            # self._component.method()
            inner_attr = next((c for c in callee.children if c.type == "attribute"), None)
            if inner_attr is not None:
                inner_idents = [c for c in inner_attr.children if c.type == "identifier"]
                if len(inner_idents) == 2 and inner_idents[0].text.decode("utf-8") == "self":
                    component = inner_idents[1].text.decode("utf-8")
                    if component in _FLOW_SKIP_OBJECTS:
                        continue
                    comp_type = component_types.get(component, component.lstrip("_"))
                    calls.append((line, f"{comp_type}.{attr}()"))
                    continue
            # self.method()
            if len(idents) == 2 and idents[0].text.decode("utf-8") == "self":
                if not attr.startswith("__"):
                    calls.append((line, f"self.{attr}()"))
        elif callee.type == "identifier":
            name = callee.text.decode("utf-8")
            if name in _FLOW_SKIP_OBJECTS:
                continue
            if name[0].isupper() or name in (
                "run_constraints",
                "extract_features",
                "compress_results",
                "build_retrieval_profile",
            ):
                calls.append((line, f"{name}()"))

    seen: set[str] = set()
    steps: list[str] = []
    for _, call in sorted(calls):
        if call not in seen:
            seen.add(call)
            steps.append(call)
    return steps


def _extract_python_regex(content: str) -> str:
    """Fallback Python extraction using regex when AST fails."""
    lines: list[str] = []

    classes = re.findall(r"^class\s+(\w+)(?:\(([^)]*)\))?:", content, re.MULTILINE)
    if classes:
        cls_strs = []
        for name, bases in classes:
            cls_strs.append(f"{name}({bases})" if bases else name)
        lines.append(f"classes: {'; '.join(cls_strs)}")

    functions = re.findall(r"^(?:async\s+)?def\s+(\w+)\(([^)]*)\)", content, re.MULTILINE)
    if functions:
        func_strs = [f"{name}({params})" for name, params in functions]
        lines.append(f"functions: {', '.join(func_strs)}")

    imports = set()
    for m in re.finditer(r"^(?:from\s+(\S+)\s+)?import\s+(\S+)", content, re.MULTILINE):
        mod = m.group(1) or m.group(2)
        imports.add(mod.split(".")[0])
    if imports:
        lines.append(f"imports: {', '.join(sorted(imports))}")

    return "\n".join(lines)


def _extract_config(suffix: str, content: str) -> str:
    """Extract top-level keys from config files."""
    try:
        if suffix in (".yaml", ".yml"):
            # Only import yaml if needed — it's optional
            import yaml

            data = yaml.safe_load(content)
        elif suffix == ".json":
            data = json.loads(content)
        elif suffix == ".toml":
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-reattr]

            data = tomllib.loads(content)
        else:
            return ""
    except Exception:
        return ""

    if isinstance(data, dict):
        keys = list(data.keys())[:20]  # Cap at 20 keys
        return f"keys: {', '.join(str(k) for k in keys)}"
    return ""


def _extract_markdown(content: str) -> str:
    """Extract headings from markdown/RST files."""
    # Markdown headings
    headings = re.findall(r"^(#{1,3})\s+(.+)", content, re.MULTILINE)
    if headings:
        items = [f"{'#' * len(h[0])} {h[1].strip()}" for h in headings[:15]]
        return f"headings: {'; '.join(items)}"

    # RST headings (line of = or - under text)
    rst_headings = re.findall(r"^(.+)\n[=\-~^]+$", content, re.MULTILINE)
    if rst_headings:
        items = [h.strip() for h in rst_headings[:15]]
        return f"headings: {'; '.join(items)}"

    return ""


def _extract_generic_code(content: str) -> str:
    """Extract structure from non-Python code using regex patterns.

    Covers: JS/TS, Go, Rust, Java/Kotlin, C/C++, Ruby, C#, Swift, PHP, etc.
    """
    lines: list[str] = []

    # Classes / structs / interfaces / traits / enums
    type_defs = re.findall(
        r"^(?:export\s+)?(?:pub\s+)?(?:public\s+|private\s+|protected\s+|abstract\s+|sealed\s+)?"
        r"(?:class|struct|interface|trait|enum|type)\s+"
        r"(\w+)",
        content,
        re.MULTILINE,
    )
    if type_defs:
        lines.append(f"types: {', '.join(dict.fromkeys(type_defs))}")

    # Functions / methods (various languages)
    func_patterns = [
        # Go: func Name(
        r"^func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(",
        # Rust: fn name(  / pub fn name(
        r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]",
        # JS/TS: function name( / export function name(
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[<(]",
        # JS/TS: const name = (...) => / const name = function
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)\s*=>|function)",
        # Java/C#/Kotlin: public void name( / fun name(
        r"^\s+(?:public|private|protected|static|override|virtual|abstract|final|\s)*"
        r"(?:fun|void|int|string|bool|float|double|var|val|Task|async)\s+(\w+)\s*[<(]",
        # Ruby: def name
        r"^(?:\s+)?def\s+(\w+)",
        # C/C++: type name( at start of line (heuristic)
        r"^(?:static\s+)?(?:inline\s+)?(?:const\s+)?\w[\w:*&<> ]*\s+(\w+)\s*\([^;]*$",
    ]
    functions: list[str] = []
    for pat in func_patterns:
        for m in re.finditer(pat, content, re.MULTILINE):
            name = m.group(1)
            if name not in functions and name not in (
                "if",
                "for",
                "while",
                "switch",
                "return",
                "main",
            ):
                functions.append(name)
    if functions:
        lines.append(f"functions: {', '.join(functions[:20])}")

    # Imports / requires / use statements
    imports: set[str] = set()
    import_patterns = [
        r'^import\s+["\']([^"\']+)["\']',  # JS/TS import "x"
        r'^import\s+.*\s+from\s+["\']([^"\']+)["\']',  # JS/TS import x from "y"
        r'^(?:const|let|var)\s+.*=\s*require\(["\']([^"\']+)["\']\)',  # Node require
        r'^import\s+"([^"]+)"',  # Go import
        r"^use\s+([\w:]+)",  # Rust use
        r"^import\s+([\w.]+)",  # Java/Kotlin
        r"^using\s+([\w.]+)",  # C#
        r'^require\s+["\']([^"\']+)["\']',  # Ruby
        r'^#include\s+[<"]([^>"]+)[>"]',  # C/C++
    ]
    for pat in import_patterns:
        for m in re.finditer(pat, content, re.MULTILINE):
            mod = m.group(1).split("/")[0].split("::")[0].split(".")[0]
            if mod:
                imports.add(mod)
    if imports:
        lines.append(f"imports: {', '.join(sorted(imports))}")

    return "\n".join(lines)


def _format_index(
    entries: list[tuple[str, str]],
    connection_counts: dict[str, int] | None = None,
    max_chars: int = _MAX_INDEX_CHARS,
) -> str:
    """Format entries into the final index text, truncating if over budget.

    Strategy: never drop files entirely.  If over budget, progressively
    reduce detail — first strip imports, then strip function lists — from
    the *least connected* files first.  Files with more import connections
    are architecturally central and keep their structural info.
    Falls back to depth-based ordering when connection data is unavailable.
    """
    parts: list[str] = []
    for rel_path, info in entries:
        parts.append(f"## {rel_path}\n{info}")

    full = "\n\n".join(parts)

    if max_chars <= 0 or len(full) <= max_chars:
        return full

    # Over budget — strip detail from least-connected files first.
    # Files with more imports to/from other files are architecturally
    # central and should keep their structural info.
    mutable = list(entries)  # preserve original order for output
    conns = connection_counts or {}
    by_priority = sorted(
        range(len(mutable)),
        key=lambda i: conns.get(mutable[i][0], 0),
    )

    # Pass 1: strip imports lines from least-connected files first
    for idx in by_priority:
        rel_path, info = mutable[idx]
        lines = [ln for ln in info.splitlines() if not ln.startswith("imports:")]
        mutable[idx] = (rel_path, "\n".join(lines))
        if _estimate_size(mutable) <= max_chars:
            break

    # Pass 2: strip functions lines from least-connected files first
    if _estimate_size(mutable) > max_chars:
        for idx in by_priority:
            rel_path, info = mutable[idx]
            lines = [ln for ln in info.splitlines() if not ln.startswith("functions:")]
            mutable[idx] = (rel_path, "\n".join(lines))
            if _estimate_size(mutable) <= max_chars:
                break

    # Pass 3: strip doc lines from least-connected files
    if _estimate_size(mutable) > max_chars:
        for idx in by_priority:
            rel_path, info = mutable[idx]
            lines = [ln for ln in info.splitlines() if not ln.startswith("doc:")]
            mutable[idx] = (rel_path, "\n".join(lines))
            if _estimate_size(mutable) <= max_chars:
                break

    # Pass 4: last resort — reduce to path-only for least-connected files
    if _estimate_size(mutable) > max_chars:
        for idx in by_priority:
            mutable[idx] = (mutable[idx][0], "")
            if _estimate_size(mutable) <= max_chars:
                break

    result_parts = []
    for rel_path, info in mutable:
        if info.strip():
            result_parts.append(f"## {rel_path}\n{info}")
        else:
            result_parts.append(f"## {rel_path}")

    return "\n\n".join(result_parts)


def _estimate_size(entries: list[tuple[str, str]]) -> int:
    """Estimate formatted index size without building the full string."""
    total = 0
    for rel_path, info in entries:
        total += 3 + len(rel_path) + 1 + len(info) + 2  # "## " + path + "\n" + info + "\n\n"
    return total


# ---------------------------------------------------------------------------
# Full import graph (for reverse-import caller expansion)
# ---------------------------------------------------------------------------


def _extract_full_imports(
    content: str,
    file_path: str = "",
) -> set[str]:
    """Extract full dotted import paths from Python source (tree-sitter, regex fallback).

    Args:
        content: Python source code.
        file_path: Relative posix path of the file (e.g. "pkg/sub/mod.py").
            Used to resolve relative imports (from .sibling import X).
    """
    tree = parse_python(content)
    if tree is None:
        return _extract_full_imports_regex(content)

    # Derive the package path for resolving relative imports.
    pkg = ""
    if file_path:
        parts = file_path.replace("\\", "/").split("/")
        if parts[-1] == "__init__.py":
            pkg = ".".join(parts[:-1])
        else:
            pkg = ".".join(parts[:-1])

    imports: set[str] = set()
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "import_statement":
            for c in n.children:
                if c.type == "dotted_name":
                    imports.add(c.text.decode("utf-8"))
                elif c.type == "aliased_import":
                    orig = next((x for x in c.children if x.type == "dotted_name"), None)
                    if orig is not None:
                        imports.add(orig.text.decode("utf-8"))
        elif n.type == "import_from_statement":
            relative = next((c for c in n.children if c.type == "relative_import"), None)
            module_node = next((c for c in n.children if c.type == "dotted_name"), None)
            if relative is not None and pkg:
                inner_dotted = next((c for c in relative.children if c.type == "dotted_name"), None)
                if inner_dotted is not None:
                    dots_text = relative.text.decode("utf-8")
                    level = 0
                    for ch in dots_text:
                        if ch == ".":
                            level += 1
                        else:
                            break
                    mod = inner_dotted.text.decode("utf-8")
                    parent = pkg
                    for _ in range(level - 1):
                        dot = parent.rfind(".")
                        if dot >= 0:
                            parent = parent[:dot]
                        else:
                            break
                    imports.add(f"{parent}.{mod}")
            elif module_node is not None:
                imports.add(module_node.text.decode("utf-8"))
        elif n.type == "future_import_statement":
            imports.add("__future__")
        stack.extend(n.children)
    return imports


def _extract_full_imports_regex(content: str) -> set[str]:
    """Regex fallback for full import paths.

    Unlike the structural index regex (top-level only), this matches
    indented imports too — critical for lazy imports inside functions.
    """
    imports: set[str] = set()
    for m in re.finditer(r"^\s*from\s+(\S+)\s+import", content, re.MULTILINE):
        imports.add(m.group(1))
    for m in re.finditer(r"^\s*import\s+(\S+)", content, re.MULTILINE):
        imports.add(m.group(1).split(",")[0].strip())
    return imports


def _build_module_file_lookup(file_list: list[str]) -> dict[str, str]:
    """Build module_dotted_path -> relative_file_path lookup.

    E.g.: "fitz_sage.governance.governor" -> "fitz_sage/governance/governor.py"
    Also maps package inits: "fitz_sage.governance" -> "fitz_sage/governance/__init__.py"
    """
    lookup: dict[str, str] = {}
    for rel_path in file_list:
        if not rel_path.endswith(".py"):
            continue
        module = rel_path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            lookup[module] = rel_path
            lookup[module[:-9]] = rel_path  # strip ".__init__"
        else:
            lookup[module] = rel_path
    return lookup


def build_import_graph(
    source_dir: str,
    file_list: list[str],
    max_file_bytes: int = 50_000,
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Build forward import map and module lookup.

    Returns:
        (forward_map, module_lookup) where:
        - forward_map: {file_path: {resolved_file_path, ...}} — only intra-project imports
        - module_lookup: {dotted.module: file_path}
    """
    root = Path(source_dir).resolve()
    module_lookup = _build_module_file_lookup(file_list)
    forward: dict[str, set[str]] = {}

    for rel_path in file_list:
        if not rel_path.endswith(".py"):
            continue
        full_path = root / rel_path
        if not full_path.is_file():
            continue
        try:
            raw = full_path.read_bytes()[:max_file_bytes]
            content = raw.decode("utf-8", errors="replace")
        except OSError:
            continue

        full_imports = _extract_full_imports(content, file_path=rel_path)
        resolved = set()
        for imp in full_imports:
            target = module_lookup.get(imp)
            if target and target != rel_path:
                resolved.add(target)

        if resolved:
            forward[rel_path] = resolved

    return forward, module_lookup


# ---------------------------------------------------------------------------
# Interface signature extraction (compact cheat sheet for planning context)
# ---------------------------------------------------------------------------

_MAX_SIGNATURES_CHARS = 8000


def _extract_signatures_from_python(content: str) -> str:
    """Extract class/function signatures with type annotations from Python source.

    Produces a compact representation showing:
    - Classes with base classes and method signatures (params + return types)
    - Top-level functions with params + return types
    """
    tree = parse_python(content)
    if tree is None:
        return ""
    root = tree.root_node
    lines: list[str] = []

    for node in _iter_top_level(root):
        if node.type == "class_definition":
            cname_node = next((c for c in node.children if c.type == "identifier"), None)
            if cname_node is None:
                continue
            cname = cname_node.text.decode("utf-8")
            bases: list[str] = []
            args = next((c for c in node.children if c.type == "argument_list"), None)
            if args is not None:
                for c in args.children:
                    if c.is_named:
                        bases.append(_node_name(c))
            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"class {cname}{base_str}:")

            for m in iter_class_methods(node):
                mname = _function_name(m)
                if mname is None:
                    continue
                params_node = next((c for c in m.children if c.type == "parameters"), None)
                params: list[str] = []
                if params_node is not None:
                    for p in params_node.children:
                        if p.type in (
                            "keyword_separator",
                            "list_splat_pattern",
                            "dictionary_splat_pattern",
                        ):
                            break
                        formatted = _formatted_param(p)
                        if formatted:
                            params.append(formatted)
                ret_node = _returns_annotation(m)
                ret_str = ""
                if ret_node is not None:
                    unparsed = unparse_annotation(ret_node) or "?"
                    ret_str = f" -> {unparsed}"
                async_prefix = "async " if _function_is_async(m) else ""
                lines.append(f"  {async_prefix}{mname}({', '.join(params)}){ret_str}")
        elif node.type == "function_definition":
            fname = _function_name(node)
            if fname is None:
                continue
            params_node = next((c for c in node.children if c.type == "parameters"), None)
            params = []
            if params_node is not None:
                for p in params_node.children:
                    if p.type in (
                        "keyword_separator",
                        "list_splat_pattern",
                        "dictionary_splat_pattern",
                    ):
                        break
                    formatted = _formatted_param(p)
                    if formatted:
                        params.append(formatted)
            ret_node = _returns_annotation(node)
            ret_str = ""
            if ret_node is not None:
                unparsed = unparse_annotation(ret_node) or "?"
                ret_str = f" -> {unparsed}"
            async_prefix = "async " if _function_is_async(node) else ""
            lines.append(f"{async_prefix}{fname}({', '.join(params)}){ret_str}")
    return "\n".join(lines)


def extract_interface_signatures(
    source_dir: str,
    file_paths: list[str],
    max_file_bytes: int = 50_000,
) -> str:
    """Extract compact interface signatures from Python files for planning context.

    Produces a cheat sheet of class hierarchies, method signatures, and return
    types that the LLM can reference without reading full source. Placed at the
    top of the planning context so it's never truncated.

    Args:
        source_dir: Absolute path to source directory root.
        file_paths: Relative posix-style paths to extract from.
        max_file_bytes: Maximum bytes to read per file.

    Returns:
        Formatted signature block, or empty string if no signatures found.
    """
    root = Path(source_dir).resolve()
    blocks: list[str] = []
    used = 0

    for rel_path in file_paths:
        if not rel_path.endswith(".py"):
            continue

        full_path = root / rel_path
        if not full_path.is_file():
            continue

        try:
            raw = full_path.read_bytes()[:max_file_bytes]
            content = raw.decode("utf-8", errors="replace")
        except OSError:
            continue

        sigs = _extract_signatures_from_python(content)
        if not sigs:
            continue

        block = f"## {rel_path}\n{sigs}"
        if used + len(block) > _MAX_SIGNATURES_CHARS:
            break

        blocks.append(block)
        used += len(block)

    return "\n\n".join(blocks)


_MAX_LIBRARY_CHARS = 4000
_MAX_LIBRARY_PACKAGES = 10


def extract_library_signatures(
    source_dir: str,
    included_files: list[str],
    file_list: list[str],
    max_file_bytes: int = 50_000,
) -> str:
    """Extract public API signatures from third-party packages used by included files.

    Collects imports from included files, filters out intra-project imports,
    then uses importlib + inspect to extract class/method signatures from
    installed packages. Gives the LLM ground truth for library APIs so it
    doesn't hallucinate non-existent methods like ``ContextVar.update()``.

    Args:
        source_dir: Absolute path to source directory root.
        included_files: Files included in the planning context.
        file_list: All file paths in the codebase (for intra-project filtering).
        max_file_bytes: Maximum bytes to read per file.

    Returns:
        Formatted library reference block, or empty string if nothing found.
    """
    import importlib
    import inspect

    root = Path(source_dir).resolve()
    module_lookup = _build_module_file_lookup(file_list)

    # Collect all imports from included files
    all_imports: set[str] = set()
    for rel_path in included_files:
        if not rel_path.endswith(".py"):
            continue
        full_path = root / rel_path
        if not full_path.is_file():
            continue
        try:
            raw = full_path.read_bytes()[:max_file_bytes]
            content = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        all_imports.update(_extract_full_imports(content))

    # Filter out intra-project imports
    third_party: set[str] = set()
    for imp in all_imports:
        top_level = imp.split(".")[0]
        if top_level in module_lookup or imp in module_lookup:
            continue
        # Skip stdlib modules we can't usefully introspect
        if top_level in {"__future__", "typing", "typing_extensions", "collections"}:
            continue
        third_party.add(top_level)

    if not third_party:
        return ""

    blocks: list[str] = []
    used = 0

    for pkg_name in sorted(third_party)[:_MAX_LIBRARY_PACKAGES]:
        try:
            mod = importlib.import_module(pkg_name)
        except Exception:
            continue

        lines: list[str] = []
        try:
            members = inspect.getmembers(mod)
        except Exception:
            members = [(name, getattr(mod, name, None)) for name in dir(mod)]

        for name, obj in members:
            if name.startswith("_"):
                continue
            try:
                if inspect.isclass(obj):
                    methods = _extract_class_public_methods(obj)
                    if methods:
                        lines.append(f"class {name}: {', '.join(methods)}")
                    else:
                        lines.append(f"class {name}")
                elif inspect.isfunction(obj) or inspect.isbuiltin(obj):
                    sig = _safe_signature(obj)
                    lines.append(f"{name}{sig}")
            except Exception:
                continue

        if not lines:
            continue

        block = f"## {pkg_name}\n" + "\n".join(lines[:20])  # Cap lines per package
        if used + len(block) > _MAX_LIBRARY_CHARS:
            break

        blocks.append(block)
        used += len(block)

    if not blocks:
        return ""

    logger.info(f"Library signatures: extracted {len(blocks)} packages ({used} chars)")
    return "\n\n".join(blocks)


def _extract_class_public_methods(cls: type) -> list[str]:
    """Extract public method names + signatures from a class."""

    methods: list[str] = []
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(cls, name)
        except Exception:
            continue
        if callable(attr) or isinstance(attr, property):
            sig = _safe_signature(attr) if callable(attr) else " (property)"
            methods.append(f"{name}{sig}")
    return methods[:15]  # Cap methods per class


def _safe_signature(obj: object) -> str:
    """Get inspect.signature() as string, or empty on failure."""
    import inspect

    try:
        sig = inspect.signature(obj)
        return str(sig)
    except (ValueError, TypeError):
        return "()"


# ---------------------------------------------------------------------------
# Investigation question generation (customized from AST data)
# ---------------------------------------------------------------------------

_MAX_CUSTOM_QUESTIONS = 3


def generate_investigation_questions(
    signatures: str,
    forward_map: dict[str, set[str]],
    reverse_count: dict[str, int],
) -> list[str]:
    """Generate codebase-specific investigation questions from structural data.

    Analyzes interface signatures and import graph to produce targeted
    questions that guide the LLM toward architectural insights it would
    otherwise miss.

    Args:
        signatures: Output of extract_interface_signatures().
        forward_map: {file: {imported_files}} from build_import_graph().
        reverse_count: {file: number_of_importers} from import graph.

    Returns:
        List of 0-3 customized question strings.
    """
    questions: list[str] = []

    # Parse signatures to find class hierarchies and method signatures
    hierarchies = _parse_class_hierarchies(signatures)
    methods_with_simple_returns = _parse_simple_return_methods(signatures)

    # 1. Class hierarchy: base class with ≥2 implementations
    for base, impls in hierarchies.items():
        if len(impls) >= 2 and len(questions) < _MAX_CUSTOM_QUESTIONS:
            impl_list = ", ".join(impls[:5])
            questions.append(
                f"Class '{base}' has {len(impls)} implementations: {impl_list}. "
                f"For each implementation, what does its key method actually do internally? "
                f"Do all implementations handle data the same way, or do some discard "
                f"information that others preserve? What data is available inside each "
                f"implementation that is NOT available to outside callers?"
            )

    # 2. Hub file: imported by many files (architecturally central)
    hub_threshold = 5
    hubs = sorted(
        ((f, c) for f, c in reverse_count.items() if c >= hub_threshold),
        key=lambda x: x[1],
        reverse=True,
    )
    for hub_file, count in hubs[:1]:  # Only ask about the top hub
        if len(questions) < _MAX_CUSTOM_QUESTIONS:
            questions.append(
                f"File '{hub_file}' is imported by {count} other files, making it "
                f"architecturally central. What interfaces does it define? What are "
                f"the exact method signatures and return types? If this file's "
                f"contracts change, what would break?"
            )

    # 3. Simple return type on complex method (data loss signal)
    for file_path, method_name, return_type, param_count in methods_with_simple_returns:
        if len(questions) < _MAX_CUSTOM_QUESTIONS:
            questions.append(
                f"Method '{method_name}' in '{file_path}' takes {param_count} "
                f"parameters but returns only '{return_type}'. What richer data "
                f"is available inside this method before the return value is "
                f"constructed? What information is discarded during the conversion "
                f"to {return_type}?"
            )

    return questions


def _parse_class_hierarchies(signatures: str) -> dict[str, list[str]]:
    """Extract {base_class: [impl1, impl2, ...]} from signatures text.

    Parses lines like:
      class OpenAIChat(ChatProvider):
      class OllamaChat(ChatProvider):
    into {"ChatProvider": ["OpenAIChat", "OllamaChat"]}
    """
    hierarchies: dict[str, list[str]] = {}
    for match in re.finditer(r"class\s+(\w+)\(([^)]+)\):", signatures):
        cls_name = match.group(1)
        bases = [b.strip() for b in match.group(2).split(",")]
        for base in bases:
            if base and base not in ("object", "ABC", "BaseModel", "Protocol"):
                hierarchies.setdefault(base, []).append(cls_name)
    return hierarchies


def _parse_simple_return_methods(
    signatures: str,
) -> list[tuple[str, str, str, int]]:
    """Find methods with simple return types that suggest data loss.

    Returns list of (file_path, method_name, return_type, param_count).
    Only includes methods returning str/bool with ≥3 parameters.
    """
    results: list[tuple[str, str, str, int]] = []
    current_file = ""
    simple_types = {"str", "bool", "int", "float", "None"}

    for line in signatures.splitlines():
        if line.startswith("## "):
            current_file = line[3:].strip()
            continue

        # Match method lines like: "  chat(prompt: str, model: str) -> str"
        # or top-level: "process(data: list, config: dict) -> bool"
        match = re.match(
            r"\s*(?:async\s+)?(\w+)\(([^)]*)\)\s*->\s*(\w+)",
            line,
        )
        if not match:
            continue

        method_name = match.group(1)
        params_str = match.group(2)
        return_type = match.group(3)

        if return_type not in simple_types:
            continue
        if method_name.startswith("_"):
            continue

        # Count parameters (split by comma, filter empty)
        params = [p.strip() for p in params_str.split(",") if p.strip()]
        if len(params) >= 3:
            results.append((current_file, method_name, return_type, len(params)))

    return results


# ---------------------------------------------------------------------------
# Two-tier directory clustering (for large codebases, ≥100 files)
# ---------------------------------------------------------------------------

_CLUSTERING_THRESHOLD = 100


def _group_by_directory(
    file_list: list[str],
    max_depth: int = 2,
) -> dict[str, list[str]]:
    """Group file paths by their directory prefix up to *max_depth* levels.

    Root-level files (no directory) are grouped under ``"(root)"``.

    Args:
        file_list: Relative posix-style paths.
        max_depth: Maximum directory depth for grouping (default 2).

    Returns:
        Mapping of directory prefix → list of file paths in that group.
    """
    groups: dict[str, list[str]] = {}
    for rel_path in file_list:
        parts = PurePosixPath(rel_path).parts
        if len(parts) <= 1:
            key = "(root)"
        else:
            key = "/".join(parts[: min(max_depth, len(parts) - 1)]) + "/"
        groups.setdefault(key, []).append(rel_path)
    return groups


def build_directory_clusters(
    source_dir: str,
    file_list: list[str],
    max_depth: int = 2,
    max_file_bytes: int = 50_000,
) -> tuple[str, dict[str, list[str]]]:
    """Build aggregated directory-level summaries for LLM directory selection.

    For each directory cluster, aggregates class names, function names, and
    imports from the files it contains (reusing ``_extract_structure``).

    Args:
        source_dir:     Absolute path to source root.
        file_list:      Relative posix-style paths.
        max_depth:      Directory grouping depth (default 2).
        max_file_bytes: Max bytes per file for extraction.

    Returns:
        Tuple of (formatted_text, groups_dict).
        *formatted_text* is the prompt-ready cluster summary.
        *groups_dict* maps dir prefix → list of file paths.
    """
    root = Path(source_dir).resolve()
    groups = _group_by_directory(file_list, max_depth)

    lines: list[str] = []
    for dir_prefix in sorted(groups):
        files = groups[dir_prefix]
        all_classes: list[str] = []
        all_functions: list[str] = []
        all_imports: set[str] = set()

        for rel_path in files:
            full_path = root / rel_path
            if not full_path.is_file():
                continue
            try:
                raw = full_path.read_bytes()[:max_file_bytes]
                content = raw.decode("utf-8", errors="replace")
            except OSError:
                continue
            if not content.strip():
                continue

            suffix = PurePosixPath(rel_path).suffix.lower()
            info = _extract_structure(suffix, content, rel_path)
            if not info:
                continue

            for line in info.splitlines():
                if line.startswith("classes:"):
                    all_classes.extend(
                        c.strip() for c in line[len("classes:") :].split(";") if c.strip()
                    )
                elif line.startswith("functions:"):
                    all_functions.extend(
                        f.strip() for f in line[len("functions:") :].split(",") if f.strip()
                    )
                elif line.startswith("imports:"):
                    all_imports.update(
                        i.strip() for i in line[len("imports:") :].split(",") if i.strip()
                    )

        entry = f"## {dir_prefix}  ({len(files)} files)"
        detail_lines: list[str] = []
        if all_classes:
            detail_lines.append(f"classes: {'; '.join(all_classes[:15])}")
        if all_functions:
            detail_lines.append(f"functions: {', '.join(all_functions[:15])}")
        if all_imports:
            detail_lines.append(f"imports: {', '.join(sorted(all_imports)[:10])}")

        if detail_lines:
            entry += "\n" + "\n".join(detail_lines)
        lines.append(entry)

    text = "\n\n".join(lines)
    logger.info(
        f"Clustered {len(file_list)} files into {len(groups)} directories ({len(text)} chars)"
    )
    return text, groups
