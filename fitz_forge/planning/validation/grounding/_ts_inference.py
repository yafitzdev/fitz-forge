# fitz_forge/planning/validation/grounding/_ts_inference.py
"""Tree-sitter implementations of grounding/inference primitives.

Parallel to ``inference.py``. Each function here has a one-to-one parity
counterpart in ``inference.py`` and is validated by ``tests/unit/
test_ts_inference_parity.py``. Once every function in ``inference.py``
has a green parity port, callers flip over and the ast path is deleted.

Tree-sitter node shapes used here (Python grammar):
    type              wraps an annotation ("x: T" → T is a `type` node)
    identifier        bare name
    attribute         dotted path, rightmost identifier is `.attr`
    generic_type      subscript form, outer identifier + type_parameter
    type_parameter    the `[...]` block, children are comma-separated `type`s
    string            forward reference like "ClassName"
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterator

from .inference import _CONTAINER_TYPES, _RETURNS_SECTION_RE

if TYPE_CHECKING:
    from tree_sitter import Node


def _first_identifier_text(node: "Node") -> str | None:
    for c in node.children:
        if c.type == "identifier":
            return c.text.decode("utf-8")
    return None


def _rightmost_attribute_name(node: "Node") -> str | None:
    """For an ``attribute`` node ``a.b.c``, return ``c``."""
    last: str | None = None
    for c in node.children:
        if c.type == "identifier":
            last = c.text.decode("utf-8")
    return last


def extract_type_name(node: "Node | None") -> str | None:
    """Tree-sitter port of ``inference.extract_type_name``.

    Matches the ast-backed function's semantics exactly:
        ``ChatRequest``          → ``ChatRequest``
        ``Optional[ChatRequest]`` → ``ChatRequest``
        ``list[ChatRequest]``    → ``list``  (container — NOT element)
        ``fitz.ChatRequest``     → ``ChatRequest``
        ``Iterator[str]``        → ``Iterator``
        ``"ChatRequest"``        → ``ChatRequest`` (forward-ref string)
    """
    if node is None:
        return None

    # Unwrap the ``type`` wrapper tree-sitter places around an annotation
    # expression. Walk down until we hit something concrete.
    if node.type == "type":
        named = [c for c in node.children if c.is_named]
        if len(named) == 1:
            return extract_type_name(named[0])
        return None

    if node.type == "identifier":
        return node.text.decode("utf-8")

    if node.type == "attribute":
        return _rightmost_attribute_name(node)

    if node.type == "generic_type":
        outer: str | None = None
        params: "Node | None" = None
        for c in node.children:
            if c.type == "identifier":
                outer = c.text.decode("utf-8")
            elif c.type == "attribute":
                outer = _rightmost_attribute_name(c)
            elif c.type == "type_parameter":
                params = c
        if outer in _CONTAINER_TYPES:
            return outer
        if params is not None:
            inner_types = [c for c in params.children if c.type == "type"]
            if inner_types:
                return extract_type_name(inner_types[0])
        return outer

    if node.type == "string":
        txt = node.text.decode("utf-8").strip()
        # strip one pair of surrounding quotes (single, double, or triple)
        for q in ('"""', "'''", '"', "'"):
            if txt.startswith(q) and txt.endswith(q) and len(txt) >= 2 * len(q):
                txt = txt[len(q) : -len(q)]
                break
        m = re.match(r"^[A-Za-z_][A-Za-z_0-9]*", txt.strip())
        return m.group(0) if m else None

    return None


_DOUBLE_QUOTED_RE = re.compile(r'"([^"\\\']*)"')


def _normalise_string_quotes(text: str) -> str:
    """Match ``ast.unparse``'s single-quote string emission.

    ``ast.unparse`` always emits simple string literals with single
    quotes, regardless of source form. We re-write each double-quoted
    literal with a clean inner body to its single-quoted equivalent.
    Strings containing ``'``, ``"`` or ``\\`` are left alone (the regex
    class rules them out) — those are cases where ``ast.unparse`` would
    keep double quotes anyway.
    """
    return _DOUBLE_QUOTED_RE.sub(r"'\1'", text)


def unparse_annotation(node: "Node | None") -> str | None:
    """Tree-sitter port of ``inference.unparse_annotation``.

    Returns the textual source of the annotation node, or None. Equivalent
    to ``ast.unparse(annotation)`` for the subset of annotation shapes we
    care about — including the quote normalisation that ast applies to
    string literals inside the annotation.
    """
    if node is None:
        return None
    # Unwrap the outer ``type`` wrapper so the returned text is just the
    # annotation body (matches ``ast.unparse`` on ``ast.Name``/``Subscript``).
    if node.type == "type":
        named = [c for c in node.children if c.is_named]
        if len(named) == 1:
            return unparse_annotation(named[0])
    return _normalise_string_quotes(node.text.decode("utf-8"))


# ---------------------------------------------------------------------------
# Call-expression analysis
# ---------------------------------------------------------------------------


def _callable_of(call: "Node") -> "Node | None":
    """Return the callee sub-node of a ``call`` node (the thing before ``(``)."""
    for c in call.children:
        if c.is_named and c.type != "argument_list":
            return c
    return None


def class_name_of_expr(node: "Node | None") -> str | None:
    """Tree-sitter port of ``inference.class_name_of_expr``.

    Only recognises ``call`` expressions whose callee looks like a class
    constructor or classmethod:
        ``ClassName(...)``            → ClassName
        ``module.ClassName(...)``     → ClassName   (dotted, uppercase attr)
        ``ClassName.from_x(...)``     → ClassName   (uppercase value, lowercase attr)
    """
    if node is None or node.type != "call":
        return None
    func = _callable_of(node)
    if func is None:
        return None

    if func.type == "identifier":
        text = func.text.decode("utf-8")
        if text[:1].isupper():
            return text
        return None

    if func.type == "attribute":
        idents = [c for c in func.children if c.type == "identifier"]
        if not idents:
            return None
        attr = idents[-1].text.decode("utf-8")
        if attr[:1].isupper():
            return attr
        # classmethod convention: only two identifiers and the first is uppercase
        if len(idents) == 2:
            head = idents[0].text.decode("utf-8")
            if head[:1].isupper():
                return head
        return None

    return None


# ---------------------------------------------------------------------------
# Function-body walker that skips nested functions
# ---------------------------------------------------------------------------


def _is_function_def(node: "Node") -> bool:
    return node.type == "function_definition"


def _function_is_async(node: "Node") -> bool:
    """True if the ``function_definition`` has an ``async`` keyword child."""
    return any(c.type == "async" for c in node.children)


def _function_body(node: "Node") -> "Node | None":
    """Return the ``block`` child of a ``function_definition`` (its body)."""
    for c in node.children:
        if c.type == "block":
            return c
    return None


def _iter_body_skipping_nested(func_def: "Node") -> Iterator["Node"]:
    """Yield every descendant of ``func_def``'s body.

    Matches the ast version's observable behaviour: the ``continue``
    guards in ``inference.py`` never actually prune ``ast.walk``'s
    traversal (walk always descends into children), so yields and
    returns inside nested ``def``s are included. We mirror that here
    for strict parity during the A/B migration; fixing this quirk is
    a separate concern, post-migration.
    """
    body = _function_body(func_def)
    if body is None:
        return
    stack: list[Node] = list(body.children)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


# ---------------------------------------------------------------------------
# Return-type inference strategies
# ---------------------------------------------------------------------------


def _infer_return_from_body(func_def: "Node") -> str | None:
    """Tree-sitter port of ``inference._infer_return_from_body``.

    Scan return statements in the function body (ignoring nested functions).
    If every return is a class constructor call with the same class name,
    return that name. Ambiguity or non-class returns bail to None.
    """
    candidates: set[str] = set()
    for child in _iter_body_skipping_nested(func_def):
        if child.type != "return_statement":
            continue
        # Find the value node (first non-punctuation named child after ``return``)
        value = None
        for c in child.children:
            if c.type == "return":
                continue
            if c.is_named:
                value = c
                break
        if value is None:
            continue  # bare return
        name = class_name_of_expr(value)
        if name is None:
            return None  # ambiguous return — bail, matches ast version
        candidates.add(name)
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _infer_return_from_yields(func_def: "Node") -> str | None:
    """Tree-sitter port of ``inference._infer_return_from_yields``.

    A function containing ``yield``/``yield from`` is an iterator;
    ``async def`` + yield → AsyncIterator, else Iterator.
    """
    for child in _iter_body_skipping_nested(func_def):
        if child.type == "yield":
            return "AsyncIterator" if _function_is_async(func_def) else "Iterator"
    return None


def _extract_docstring(func_def: "Node") -> str | None:
    """Return the textual content of the function's docstring, or None.

    Mirrors ``ast.get_docstring``: the first statement in the body is an
    expression_statement containing a single string literal.
    """
    body = _function_body(func_def)
    if body is None:
        return None
    first_stmt = next((c for c in body.children if c.is_named), None)
    if first_stmt is None or first_stmt.type != "expression_statement":
        return None
    # Its single named child must be a string
    inner = next((c for c in first_stmt.children if c.is_named), None)
    if inner is None or inner.type != "string":
        return None
    # Concatenate string_content children (handles triple-quoted and
    # concatenated adjacent literals)
    parts: list[str] = []
    for c in inner.children:
        if c.type == "string_content":
            parts.append(c.text.decode("utf-8"))
    if not parts:
        # Fallback: strip outer quote markers from .text
        raw = inner.text.decode("utf-8")
        for q in ('"""', "'''", '"', "'"):
            if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                return raw[len(q) : -len(q)]
        return raw
    return "".join(parts)


def _infer_return_from_docstring(
    func_def: "Node", known_classes: set[str]
) -> str | None:
    """Tree-sitter port of ``inference._infer_return_from_docstring``."""
    doc = _extract_docstring(func_def)
    if not doc:
        return None
    m = _RETURNS_SECTION_RE.search(doc)
    if not m:
        return None
    candidate = m.group(1)
    if candidate in known_classes:
        return candidate
    return None


def _returns_annotation(func_def: "Node") -> "Node | None":
    """Return the return-type annotation node of a function_definition, or None.

    In tree-sitter, the return type (``-> X``) appears as a ``type`` node
    between the parameters and the ``:``.
    """
    saw_params = False
    for c in func_def.children:
        if c.type == "parameters":
            saw_params = True
            continue
        if saw_params and c.type == "type":
            return c
    return None


def infer_return_type(
    func_def: "Node",
    known_classes: set[str] | None = None,
) -> str | None:
    """Tree-sitter port of ``inference.infer_return_type``.

    Strategy order matches the ast version exactly:
        1. Explicit ``-> T`` annotation
        2. ``return ClassName(...)`` in the body
        3. Function contains ``yield`` → Iterator/AsyncIterator
        4. Docstring ``Returns: ClassName`` (gated by ``known_classes``)
    """
    ret_node = _returns_annotation(func_def)
    if ret_node is not None:
        unparsed = unparse_annotation(ret_node)
        if unparsed:
            return unparsed

    body_ret = _infer_return_from_body(func_def)
    if body_ret:
        return body_ret

    yield_ret = _infer_return_from_yields(func_def)
    if yield_ret:
        return yield_ret

    if known_classes:
        doc_ret = _infer_return_from_docstring(func_def, known_classes)
        if doc_ret:
            return doc_ret

    return None


# ---------------------------------------------------------------------------
# Class-body inspection
# ---------------------------------------------------------------------------


def _class_body(class_def: "Node") -> "Node | None":
    for c in class_def.children:
        if c.type == "block":
            return c
    return None


def _annotated_assignment_parts(
    assign: "Node",
) -> tuple["Node | None", "Node | None", "Node | None"]:
    """Given an ``assignment`` node, return (target, type_annotation, value).

    Tree-sitter represents ``x: T = v`` as a single ``assignment`` with
    children ``identifier : type = expr``. For ``x: T`` (no value) the
    ``= expr`` tail is omitted.
    """
    target: Node | None = None
    type_ann: Node | None = None
    value: Node | None = None
    saw_colon = False
    saw_equals = False
    for c in assign.children:
        if not c.is_named and c.type == ":":
            saw_colon = True
            continue
        if not c.is_named and c.type == "=":
            saw_equals = True
            continue
        if target is None and c.is_named:
            target = c
            continue
        if saw_colon and not saw_equals and c.type == "type":
            type_ann = c
            continue
        if saw_equals and c.is_named:
            value = c
            continue
    return target, type_ann, value


def extract_class_fields(class_def: "Node") -> dict[str, str]:
    """Tree-sitter port of ``inference.extract_class_fields``.

    Returns ``{field_name: type_name}`` for top-level annotated attributes
    on a class. Skips:
        - Methods / nested classes
        - ClassVar annotations
        - Non-identifier targets (subscripts, attributes)
    """
    fields: dict[str, str] = {}
    body = _class_body(class_def)
    if body is None:
        return fields
    for stmt in body.children:
        if stmt.type != "expression_statement":
            continue
        inner = next((c for c in stmt.children if c.is_named), None)
        if inner is None or inner.type != "assignment":
            continue
        target, type_ann, _value = _annotated_assignment_parts(inner)
        if target is None or type_ann is None:
            continue
        if target.type != "identifier":
            continue
        ann_text = unparse_annotation(type_ann) or ""
        if "ClassVar" in ann_text:
            continue
        t = extract_type_name(type_ann)
        if t:
            fields[target.text.decode("utf-8")] = t
    return fields


# ---------------------------------------------------------------------------
# self._attr tracking
# ---------------------------------------------------------------------------


def _attribute_self_target(node: "Node") -> str | None:
    """If ``node`` is ``self.attr``, return ``attr``. Otherwise None."""
    if node.type != "attribute":
        return None
    idents = [c for c in node.children if c.type == "identifier"]
    if len(idents) != 2:
        return None
    if idents[0].text.decode("utf-8") != "self":
        return None
    return idents[1].text.decode("utf-8")


def _find_method(class_def: "Node", method_name: str) -> "Node | None":
    body = _class_body(class_def)
    if body is None:
        return None
    for c in body.children:
        if c.type == "function_definition":
            for ch in c.children:
                if ch.type == "identifier":
                    if ch.text.decode("utf-8") == method_name:
                        return c
                    break
    return None


def _init_param_types(init_def: "Node") -> dict[str, str]:
    """Return {param_name: type_name} from an __init__'s typed_parameters."""
    out: dict[str, str] = {}
    for c in init_def.children:
        if c.type != "parameters":
            continue
        for p in c.children:
            if p.type != "typed_parameter":
                continue
            # child 0 usually identifier or *identifier etc; we want the name
            name_node = next((ch for ch in p.children if ch.type == "identifier"), None)
            type_node = next((ch for ch in p.children if ch.type == "type"), None)
            if name_node is None or type_node is None:
                continue
            t = extract_type_name(type_node)
            if t:
                out[name_node.text.decode("utf-8")] = t
    return out


def extract_init_self_attrs(
    class_def: "Node",
    known_classes: set[str] | None = None,
) -> dict[str, str]:
    """Tree-sitter port of ``inference.extract_init_self_attrs``.

    Three type sources (matches ast version's pass order):
      0. Class-level ``_x: Type`` annotations (no value required)
      1. ``self._x = param`` where ``param`` is annotated in __init__
      2. ``self._x = ClassName(...)`` / ``self._x: Type = ...``
    """
    attrs: dict[str, str] = {}

    # Pass 0: class-level annotations
    body = _class_body(class_def)
    if body is None:
        return attrs
    for stmt in body.children:
        if stmt.type != "expression_statement":
            continue
        inner = next((c for c in stmt.children if c.is_named), None)
        if inner is None or inner.type != "assignment":
            continue
        target, type_ann, _value = _annotated_assignment_parts(inner)
        if target is None or type_ann is None:
            continue
        if target.type != "identifier":
            continue
        t = extract_type_name(type_ann)
        if t:
            attrs[target.text.decode("utf-8")] = t

    init_def = _find_method(class_def, "__init__")
    if init_def is None:
        return attrs

    param_types = _init_param_types(init_def)

    # Walk the init body for self.* assignments (including nested blocks,
    # matching ast.walk). Nested function_definitions are skipped by
    # _iter_body_skipping_nested.
    for stmt in _iter_body_skipping_nested(init_def):
        if stmt.type != "assignment":
            continue
        target, type_ann, value = _annotated_assignment_parts(stmt)
        if target is None:
            continue
        attr_name = _attribute_self_target(target) if target.type == "attribute" else None
        if attr_name is None:
            continue

        # self._x: T = ...  (annotated assignment wins over value inference)
        if type_ann is not None:
            t = extract_type_name(type_ann)
            if t:
                attrs[attr_name] = t
                continue

        if value is None:
            continue

        # self._x = some_param
        if value.type == "identifier":
            pname = value.text.decode("utf-8")
            if pname in param_types:
                attrs[attr_name] = param_types[pname]
                continue
        # self._x = ClassName(...) / module.ClassName(...) / ClassName.from_x(...)
        if value.type == "call":
            cname = class_name_of_expr(value)
            if cname and (known_classes is None or cname in known_classes):
                attrs[attr_name] = cname

    return attrs


# ---------------------------------------------------------------------------
# Top-level index augmentation (tree-sitter)
# ---------------------------------------------------------------------------


def _function_name(func_def: "Node") -> str | None:
    """Return the function name identifier text, or None."""
    for c in func_def.children:
        if c.type == "identifier":
            return c.text.decode("utf-8")
    return None


def _class_name(class_def: "Node") -> str | None:
    for c in class_def.children:
        if c.type == "identifier":
            return c.text.decode("utf-8")
    return None


def _class_bases(class_def: "Node") -> list[str]:
    """Return base class names from a ``class_definition``'s argument_list."""
    args = next((c for c in class_def.children if c.type == "argument_list"), None)
    if args is None:
        return []
    bases: list[str] = []
    for c in args.children:
        if not c.is_named:
            continue
        if c.type == "identifier":
            bases.append(c.text.decode("utf-8"))
        elif c.type == "attribute":
            idents = [x for x in c.children if x.type == "identifier"]
            if idents:
                bases.append(idents[-1].text.decode("utf-8"))
        elif c.type == "subscript":
            # Base[generic] — unwrap to the outer name
            v = next((x for x in c.children if x.type in ("identifier", "attribute")), None)
            if v is not None:
                if v.type == "identifier":
                    bases.append(v.text.decode("utf-8"))
                else:
                    idents = [x for x in v.children if x.type == "identifier"]
                    if idents:
                        bases.append(idents[-1].text.decode("utf-8"))
    return bases


def _extract_param_names(func_def: "Node") -> list[str]:
    """Replicate ast version's param collection: positional + kwonly flat,
    ``*vararg``/``**kwarg`` prefixed with ``*``/``**``.
    """
    params_node = next((c for c in func_def.children if c.type == "parameters"), None)
    if params_node is None:
        return []
    out: list[str] = []
    for p in params_node.children:
        if p.type == "identifier":
            out.append(p.text.decode("utf-8"))
        elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                out.append(ident.text.decode("utf-8"))
        elif p.type == "list_splat_pattern":
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                out.append(f"*{ident.text.decode('utf-8')}")
        elif p.type == "dictionary_splat_pattern":
            ident = next((c for c in p.children if c.type == "identifier"), None)
            if ident is not None:
                out.append(f"**{ident.text.decode('utf-8')}")
    return out


def _unwrap_decorated(node: "Node") -> "Node":
    """Tree-sitter wraps ``@dec\ndef foo`` in a ``decorated_definition``.

    ast treats decorators as attributes of the FunctionDef/ClassDef, so
    to get parity we unwrap ``decorated_definition`` to its inner def.
    """
    if node.type == "decorated_definition":
        for c in node.children:
            if c.type in ("function_definition", "class_definition"):
                return c
    return node


def iter_all_classes(root: "Node") -> Iterator["Node"]:
    """Yield every ``class_definition`` in the tree (nested included).

    Mirrors ``ast.walk(tree)`` filtered to ``ast.ClassDef`` — which also
    descends into classes and functions looking for nested class defs.
    Deduplicates decorated classes: ``decorated_definition`` wraps an
    inner ``class_definition`` in tree-sitter, so we must yield one
    without re-yielding the other when the tree is walked.
    """
    stack: list[Node] = [root]
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if n.type == "class_definition" and n.id not in seen:
            seen.add(n.id)
            yield n
        elif n.type == "decorated_definition":
            inner = _unwrap_decorated(n)
            if inner.type == "class_definition" and inner.id not in seen:
                seen.add(inner.id)
                yield inner
        stack.extend(n.children)


def iter_top_level_functions(root: "Node") -> Iterator["Node"]:
    """Yield sync function_definition nodes at module top level (col_offset == 0).

    Unwraps ``decorated_definition`` wrappers. Skips ``async def`` — the
    ast pass1 indexes only ``ast.FunctionDef``, never ``AsyncFunctionDef``,
    so top-level async functions stay out of the index. We preserve that
    quirk for strict byte-parity; fixing it is a separate post-migration
    change.
    """
    for c in root.children:
        candidate: "Node | None" = None
        if c.type == "function_definition" and c.start_point[1] == 0:
            candidate = c
        elif c.type == "decorated_definition" and c.start_point[1] == 0:
            inner = _unwrap_decorated(c)
            if inner.type == "function_definition":
                candidate = inner
        if candidate is None:
            continue
        if _function_is_async(candidate):
            continue
        yield candidate


def iter_class_methods(class_def: "Node") -> Iterator["Node"]:
    """Yield direct function_definition children of a class's body block.

    Unwraps ``decorated_definition`` so decorated methods aren't missed.
    """
    body = _class_body(class_def)
    if body is None:
        return
    for c in body.children:
        if c.type == "function_definition":
            yield c
        elif c.type == "decorated_definition":
            inner = _unwrap_decorated(c)
            if inner.type == "function_definition":
                yield inner


def absorb_file_pass1(lookup, rel: str, root: "Node") -> int:
    """Tree-sitter port of ``StructuralIndexLookup._absorb_file_pass1``.

    Takes the lookup instance so we don't have to duplicate the index
    data structures. Same return value: number of new classes added.
    """
    from .index import IndexedFunction

    added = 0
    for class_node in iter_all_classes(root):
        absorb_class(lookup, rel, class_node)
        added += 1
    for func_node in iter_top_level_functions(root):
        name = _function_name(func_node)
        if name is None:
            continue
        if name in lookup._all_function_names:
            continue
        params = _extract_param_names(func_node)
        func = IndexedFunction(name, rel, params, None)
        lookup.functions.setdefault(name, []).append(func)
        lookup._all_function_names.add(name)
    return added


def absorb_file_pass2(lookup, rel: str, root: "Node", known_classes: set[str]) -> None:
    """Tree-sitter port of ``StructuralIndexLookup._absorb_file_pass2``."""
    for func_node in iter_top_level_functions(root):
        name = _function_name(func_node)
        if name is None:
            continue
        funcs = lookup.functions.get(name, [])
        for f in funcs:
            if f.file == rel and f.return_type is None:
                ret = infer_return_type(func_node, known_classes)
                if ret:
                    f.return_type = ret
                break


def absorb_class(lookup, rel: str, class_node: "Node") -> None:
    """Tree-sitter port of ``StructuralIndexLookup._absorb_class``."""
    from .index import IndexedClass, IndexedMethod

    name = _class_name(class_node)
    if name is None:
        return

    methods: dict[str, IndexedMethod] = {}
    for m_node in iter_class_methods(class_node):
        mname = _function_name(m_node)
        if mname is None:
            continue
        if mname.startswith("__") and mname != "__init__":
            continue
        ret_node = _returns_annotation(m_node)
        ret = unparse_annotation(ret_node) if ret_node is not None else None
        if ret is None:
            ret = _infer_return_from_body(m_node) or _infer_return_from_yields(m_node)
        methods[mname] = IndexedMethod(mname, ret)

    fields = extract_class_fields(class_node)
    bases = _class_bases(class_node)

    if name in lookup._all_class_names:
        for existing_cls in lookup.classes.get(name, []):
            for mname, minfo in methods.items():
                if mname not in existing_cls.methods:
                    existing_cls.methods[mname] = minfo
                    lookup._all_method_names.add(mname)
            for fname, ftype in fields.items():
                existing_cls.fields.setdefault(fname, ftype)
            for b in bases:
                if b not in existing_cls.bases:
                    existing_cls.bases.append(b)
    else:
        cls = IndexedClass(name, rel, bases, methods, fields, [])
        lookup.classes.setdefault(name, []).append(cls)
        lookup._all_class_names.add(name)
        for mname in methods:
            lookup._all_method_names.add(mname)


def augment_from_source_dir(lookup, source_dir: str) -> int:
    """Tree-sitter port of ``StructuralIndexLookup.augment_from_source_dir``."""
    from pathlib import Path

    from ._ts_parser import _parse_or_none

    root = Path(source_dir)
    if not root.is_dir():
        return 0

    parsed: list[tuple[str, "Node"]] = []
    added = 0
    for py_file in root.rglob("*.py"):
        rel = str(py_file.relative_to(root)).replace("\\", "/")
        if ".venv" in rel or "__pycache__" in rel:
            continue
        try:
            text = py_file.read_bytes()[:200_000].decode("utf-8", errors="replace")
        except OSError:
            continue
        tree = _parse_or_none(text)
        if tree is None:
            continue
        parsed.append((rel, tree.root_node))
        added += absorb_file_pass1(lookup, rel, tree.root_node)

    known_classes = set(lookup._all_class_names)
    for rel, root_node in parsed:
        absorb_file_pass2(lookup, rel, root_node, known_classes)

    return added
