# fitz_forge/planning/validation/scoring.py
"""
Deterministic plan scoring (library layer).

Pure-Python, no LLM calls. Extracted from `benchmarks/eval_v2_deterministic.py`
so the CLI can render a live quality score on plan completion without
depending on the benchmarks package.

Exposes:

- `check_single_artifact(artifact, lookup, task_requires_streaming=True)`
  Per-artifact AST validation producing an :class:`ArtifactCheck`.
- `check_all_artifacts_v2(artifacts, structural_index, ...)`
  Batch wrapper around :func:`check_single_artifact`.
- `check_cross_artifact_consistency(artifacts, artifact_checks, ...)`
  Cross-artifact consistency checks producing :class:`ConsistencyResult`s.
- `score_plan_live(plan_data, structural_index="", source_dir="")`
  Thin wrapper that returns `{"artifact_quality": .., "consistency": .., "total": ..}`
  on the 0-70 scale used by the live CLI (artifact_quality is 0-50, consistency
  is 0-20). Never raises — on error returns total=None.

The benchmark scorer (`benchmarks/eval_v2_deterministic.py`) re-exports the
AST + consistency helpers from here so its behaviour is unchanged.
"""

from __future__ import annotations

import ast
import logging
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any

from fitz_forge.planning.validation.grounding import (
    StructuralIndexLookup,
    Violation,
    check_artifact,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses (library-layer, no Pydantic dependency)
# ---------------------------------------------------------------------------


@dataclass
class ArtifactCheck:
    """AST validation result for a single artifact."""

    filename: str
    content_lines: int = 0
    parseable: bool = False
    fabricated_self_methods: int = 0
    fabricated_chained_methods: int = 0
    fabricated_field_access: int = 0
    fabricated_classes: int = 0
    has_yield: bool | None = None
    has_correct_return_type: bool | None = None
    has_not_implemented: bool = False
    has_sys_stdout: bool = False
    violation_count: int = 0
    checks_passed: int = 0
    checks_total: int = 0
    score: float = 0.0


@dataclass
class ConsistencyResult:
    """Cross-artifact consistency check result."""

    check: str
    passed: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Per-artifact checks
# ---------------------------------------------------------------------------

_YIELD_RE = re.compile(r"\byield\b")
_NOT_IMPLEMENTED_RE = re.compile(r"\bNotImplementedError\b")
_SYS_STDOUT_RE = re.compile(r"\bsys\.stdout\b")

# Language-aware stub-detection: Python's NotImplementedError has exact
# analogues in TS/JS ("Not implemented" exception message) and catch-all
# TODO markers across any language. For non-Python files we can't rely
# on the NotImplementedError symbol, so fall back to a broader pattern
# that matches the intent rather than the specific Python API.
_STUB_MARKER_RE = re.compile(
    r"""
    \bNotImplementedError\b                          # Python
    | throw\s+new\s+Error\s*\(\s*['"].*?not\s+implemented['"]  # TS/JS
    | \bTODO:\s*implement\b                          # language-agnostic stub comment
    """,
    re.VERBOSE | re.IGNORECASE,
)

_STREAMING_INDICATORS = ("engine.py", "synthesizer.py")

# Files that the deterministic scorer can reasonably analyze with the
# Python AST + Python-fabrication regexes. Everything else is checked
# only with language-agnostic heuristics (non-empty, no stub markers)
# — the scorer otherwise flags every TS/Prisma file as "unparseable"
# and docks 10+ points per artifact for Python-specific concerns that
# don't apply. Tier-2 taxonomy scoring (via Sonnet) handles semantic
# quality for all languages; Tier-1 is for structural sanity only.
_PYTHON_EXTENSIONS = frozenset({".py"})


def _is_python_file(filename: str) -> bool:
    """True when the filename's extension marks it as Python source.

    Files with no extension (unusual but possible for scripts) are
    assumed Python — conservative default that preserves behavior for
    any edge case in the existing Python-heavy benchmarks.
    """
    lower = filename.lower()
    if "." not in lower.split("/")[-1]:
        return True
    return any(lower.endswith(ext) for ext in _PYTHON_EXTENSIONS)

_SELF_METHOD_RE = re.compile(r"self\.([a-zA-Z_]\w*)\s*\(")
_CLASS_CTOR_RE = re.compile(r"\b([A-Z][a-zA-Z0-9_]*)\s*\(")

_CLASS_SKIP = frozenset(
    {
        "True",
        "False",
        "None",
        "Any",
        "Optional",
        "Union",
        "List",
        "Dict",
        "Set",
        "Tuple",
        "Type",
        "Callable",
        "Iterator",
        "Generator",
        "AsyncGenerator",
        "AsyncIterator",
        "Sequence",
        "Mapping",
        "Iterable",
        "BaseModel",
        "Field",
        "ConfigDict",
        "Path",
        "StreamingResponse",
        "EventSourceResponse",
        "JSONResponse",
        "HTTPException",
        "Response",
        "APIRouter",
        "Request",
        "Depends",
        "BackgroundTasks",
        "Body",
        "Header",
        "Cookie",
        "Form",
        "File",
        "UploadFile",
        "ThreadPoolExecutor",
        "ProcessPoolExecutor",
        "Lock",
        "Event",
        "Thread",
        "Queue",
        "Enum",
        "IntEnum",
        "ABC",
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
    }
)


def _count_violations_by_kind(violations: list[Violation], kind: str) -> int:
    return sum(1 for v in violations if v.kind == kind)


def _strip_comments(content: str) -> str:
    """Remove Python comments and docstrings from content for regex scanning."""
    lines = []
    in_docstring = False
    docstring_char = ""
    for line in content.split("\n"):
        stripped = line.strip()
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_char = stripped[:3]
                if stripped.count(docstring_char) >= 2 and len(stripped) > 3:
                    continue
                in_docstring = True
                continue
        else:
            if docstring_char in stripped:
                in_docstring = False
            continue
        if "#" in line:
            code_part = line.split("#")[0]
            lines.append(code_part)
        else:
            lines.append(line)
    return "\n".join(lines)


def _regex_fabrication_scan(
    content: str,
    lookup: StructuralIndexLookup,
) -> tuple[int, int, int, int]:
    """Detect fabrications via regex when AST parsing fails.

    Returns (fab_self, fab_chained, fab_field, fab_class).
    """
    code_only = _strip_comments(content)

    fab_self = 0
    fab_class = 0

    local_defs: set[str] = set()
    for line in code_only.split("\n"):
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            name = stripped.split("def ", 1)[1].split("(")[0].strip()
            local_defs.add(name)
        elif stripped.startswith("class "):
            name = stripped.split("class ", 1)[1].split("(")[0].split(":")[0].strip()
            local_defs.add(name)

    for m in _SELF_METHOD_RE.finditer(code_only):
        method_name = m.group(1)
        if method_name.startswith("__"):
            continue
        if method_name in local_defs:
            continue
        if not lookup.method_exists_anywhere(method_name):
            if method_name not in (
                "get",
                "set",
                "items",
                "keys",
                "values",
                "append",
                "extend",
                "update",
                "pop",
                "format",
                "join",
                "split",
                "strip",
                "encode",
                "decode",
                "lower",
                "upper",
                "startswith",
                "endswith",
                "replace",
            ):
                fab_self += 1

    for m in _CLASS_CTOR_RE.finditer(code_only):
        class_name = m.group(1)
        if class_name in _CLASS_SKIP:
            continue
        if class_name in local_defs:
            continue
        if not lookup.class_exists(class_name):
            fab_class += 1

    return fab_self, 0, 0, fab_class


def _try_parse(content: str) -> tuple[ast.Module | None, str | None]:
    """Try to parse content as Python, with recovery for code fragments."""
    try:
        return ast.parse(content), None
    except SyntaxError:
        pass

    dedented = textwrap.dedent(content)
    if dedented != content:
        try:
            return ast.parse(dedented), dedented
        except SyntaxError:
            pass

    try:
        wrapped = "class _Wrapper:\n" + content
        return ast.parse(wrapped), wrapped
    except SyntaxError:
        pass

    try:
        wrapped = "class _Wrapper:\n" + textwrap.indent(dedented, "    ")
        return ast.parse(wrapped), wrapped
    except SyntaxError:
        pass

    return None, None


def check_single_artifact(
    artifact: dict,
    lookup: StructuralIndexLookup,
    task_requires_streaming: bool = True,
) -> ArtifactCheck:
    """Run all deterministic checks on a single artifact.

    For non-Python files (TypeScript, Prisma, etc.), skips Python-specific
    checks (AST parse, fabrication regex, yield/return-type) and only
    applies language-agnostic heuristics. Tier-2 taxonomy scoring (Sonnet)
    handles semantic quality for all languages. Without this branch,
    every TS file was docked for being "unparseable" by the Python AST
    and for lacking Python-shape fabrications — artificially capping
    TS-heavy benchmark scores around 70–80 regardless of plan quality.
    """
    filename = artifact.get("filename", "unknown")
    content = artifact.get("content", "")
    is_python = _is_python_file(filename)

    if is_python:
        tree, recovered_content = _try_parse(content)
        parseable = tree is not None

        if recovered_content is not None:
            violations = check_artifact({"filename": filename, "content": recovered_content}, lookup)
        else:
            violations = check_artifact(artifact, lookup)
    else:
        # Non-Python files: the Python AST + grounding check don't apply.
        # Parseability is "not applicable" — report True so the scoring
        # weight doesn't penalize presence. Violations are empty; the
        # Python-fabrication regex is skipped below.
        tree = None
        parseable = True
        violations = []

    has_yield = None
    has_correct_return_type = None

    is_streaming_file = is_python and any(
        filename.endswith(ind) for ind in _STREAMING_INDICATORS
    )
    if task_requires_streaming and is_streaming_file and tree is not None:
        has_yield = bool(_YIELD_RE.search(content))

    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_streaming_file and "stream" in node.name.lower():
                    if node.returns:
                        ret_str = ast.dump(node.returns)
                        good_returns = ("Iterator", "Generator", "AsyncGenerator", "AsyncIterator")
                        bad_returns = ("Answer", "str", "dict")
                        if any(r in ret_str for r in good_returns):
                            has_correct_return_type = True
                        elif any(r in ret_str for r in bad_returns):
                            has_correct_return_type = False

    # Language-aware stub detection: the Python-only regex is a subset
    # of _STUB_MARKER_RE, so the broader pattern is a safe replacement
    # for every language we score.
    has_not_implemented = bool(_STUB_MARKER_RE.search(content))
    # sys.stdout is Python-specific (MCP stdio concern); non-Python
    # files don't have this class of bug.
    has_sys_stdout = bool(_SYS_STDOUT_RE.search(content)) if is_python else False

    if is_python:
        fab_self = _count_violations_by_kind(violations, "missing_method")
        fab_field = _count_violations_by_kind(violations, "wrong_field")
        fab_class = _count_violations_by_kind(violations, "missing_class")
        fab_chained = sum(
            1
            for v in violations
            if v.kind == "missing_method" and "." in v.symbol and v.symbol.count(".") >= 2
        )
        fab_self -= fab_chained

        if not parseable and len(violations) <= 1:
            r_self, r_chain, r_field, r_class = _regex_fabrication_scan(content, lookup)
            fab_self = max(fab_self, r_self)
            fab_chained = max(fab_chained, r_chain)
            fab_field = max(fab_field, r_field)
            fab_class = max(fab_class, r_class)
    else:
        fab_self = 0
        fab_field = 0
        fab_class = 0
        fab_chained = 0

    weighted_checks: list[tuple[str, float, float]] = []

    weighted_checks.append(("parseable", 1.0 if parseable else 0.0, 10.0))

    total_fab = fab_self + fab_chained + fab_field + fab_class
    if total_fab == 0:
        fab_val = 1.0
    elif total_fab == 1:
        fab_val = 0.7
    elif total_fab == 2:
        fab_val = 0.4
    elif total_fab == 3:
        fab_val = 0.2
    else:
        fab_val = 0.0
    weighted_checks.append(("no_fabrications", fab_val, 50.0))

    weighted_checks.append(("no_not_implemented", 0.0 if has_not_implemented else 1.0, 10.0))
    weighted_checks.append(("no_sys_stdout", 0.0 if has_sys_stdout else 1.0, 10.0))

    yield_val = 1.0
    if has_yield is not None:
        yield_val = 1.0 if has_yield else 0.0
    weighted_checks.append(("has_yield", yield_val, 10.0))

    ret_val = 1.0
    if has_correct_return_type is not None:
        ret_val = 1.0 if has_correct_return_type else 0.0
    weighted_checks.append(("correct_return_type", ret_val, 10.0))

    total_weight = sum(w for _, _, w in weighted_checks)
    score = (
        sum(v * w for _, v, w in weighted_checks) / total_weight * 100 if total_weight > 0 else 0.0
    )

    checks_passed = sum(1 for _, v, _ in weighted_checks if v >= 1.0)
    checks_total = len(weighted_checks)

    return ArtifactCheck(
        filename=filename,
        content_lines=content.count("\n") + 1 if content.strip() else 0,
        parseable=parseable,
        fabricated_self_methods=fab_self,
        fabricated_chained_methods=fab_chained,
        fabricated_field_access=fab_field,
        fabricated_classes=fab_class,
        has_yield=has_yield,
        has_correct_return_type=has_correct_return_type,
        has_not_implemented=has_not_implemented,
        has_sys_stdout=has_sys_stdout,
        violation_count=len(violations),
        checks_passed=checks_passed,
        checks_total=checks_total,
        score=round(score, 1),
    )


def check_all_artifacts_v2(
    artifacts: list[dict],
    structural_index: str,
    task_requires_streaming: bool = True,
    source_dir: str = "",
) -> list[ArtifactCheck]:
    """Run per-artifact checks on all artifacts."""
    lookup = StructuralIndexLookup(structural_index)
    if source_dir:
        added = lookup.augment_from_source_dir(source_dir)
        if added:
            logger.info(f"Augmented index with {added} classes from {source_dir}")
    return [check_single_artifact(a, lookup, task_requires_streaming) for a in artifacts]


# ---------------------------------------------------------------------------
# Cross-artifact consistency checks
# ---------------------------------------------------------------------------

_METHOD_CALL_RE = re.compile(r"(?:self\.)?(\w+)\.(\w+)\s*\(")
_RETURN_TYPE_RE = re.compile(r"->\s*([A-Za-z_][\w\[\], ]*)")


def _extract_method_definitions(content: str) -> dict[str, str | None]:
    """Extract defined method names and their return types from artifact content."""
    methods: dict[str, str | None] = {}
    for attempt in [
        content,
        textwrap.dedent(content),
        "class _:\n    " + content.replace("\n", "\n    "),
    ]:
        try:
            tree = ast.parse(attempt)
            break
        except SyntaxError:
            continue
    else:
        return methods

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ret_type = None
            if node.returns:
                ret_type = ast.unparse(node.returns)
            methods[node.name] = ret_type
    return methods


def _extract_method_calls(content: str) -> list[tuple[str, str]]:
    """Extract (object, method_name) pairs from method calls in content."""
    return _METHOD_CALL_RE.findall(content)


def _normalize_artifact_filename(filename: str) -> str:
    """Normalize an artifact filename for comparison."""
    return filename.replace("\\", "/").strip("/")


def check_cross_artifact_consistency(
    artifacts: list[dict],
    artifact_checks: list[ArtifactCheck] | None = None,
    structural_index: str = "",
    source_dir: str = "",
) -> list[ConsistencyResult]:
    """Check consistency across artifacts."""
    results: list[ConsistencyResult] = []

    codebase_methods: set[str] = set()
    if structural_index or source_dir:
        lookup = StructuralIndexLookup(structural_index)
        if source_dir:
            lookup.augment_from_source_dir(source_dir)
        codebase_methods = lookup._all_method_names | lookup._all_function_names

    unparseable: set[str] = set()
    if artifact_checks:
        for ac in artifact_checks:
            if not ac.parseable:
                unparseable.add(ac.filename)

    defined_methods: dict[str, dict[str, str | None]] = {}
    called_methods: dict[str, list[tuple[str, str]]] = {}

    for art in artifacts:
        fn = art.get("filename", "unknown")
        content = art.get("content", "")
        defined_methods[fn] = _extract_method_definitions(content)
        called_methods[fn] = _extract_method_calls(content)

    all_defined = set()
    for methods in defined_methods.values():
        all_defined.update(methods.keys())

    for caller_file, calls in called_methods.items():
        for obj_name, method_name in calls:
            if method_name.startswith("_") or obj_name in (
                "self",
                "json",
                "os",
                "re",
                "logging",
                "logger",
                "asyncio",
                "str",
                "list",
                "dict",
                "set",
                "Path",
                "app",
                "router",
                "request",
                "response",
            ):
                continue
            _STDLIB_METHODS = {
                "append", "extend", "pop", "insert", "remove", "clear",
                "copy", "sort", "reverse", "update", "keys", "values",
                "items", "get", "post", "put", "delete", "patch",
                "head", "options", "add", "discard", "union",
                "intersection", "difference", "format", "encode",
                "decode", "strip", "split", "join", "replace",
                "startswith", "endswith", "lower", "upper",
            }
            if method_name in _STDLIB_METHODS:
                continue
            if method_name not in all_defined:
                if method_name in codebase_methods:
                    continue
                for target_file, target_methods in defined_methods.items():
                    if target_file == caller_file:
                        continue
                    if target_file in unparseable:
                        continue
                    target_basename = target_file.rsplit("/", 1)[-1].replace(".py", "")
                    obj_clean = obj_name.lstrip("_")
                    if obj_clean.lower() == target_basename.lower():
                        available = sorted(target_methods.keys())
                        results.append(
                            ConsistencyResult(
                                check="method_name_agreement",
                                passed=False,
                                detail=(
                                    f"{caller_file} calls {obj_name}.{method_name}() but "
                                    f"{target_file} defines: {', '.join(available) if available else '(no methods)'}"
                                ),
                            )
                        )

    stream_base_types: dict[str, list[tuple[str, str, str]]] = {}
    for fn, methods in defined_methods.items():
        for method_name, ret_type in methods.items():
            if ret_type and "stream" in method_name.lower():
                stream_base_types.setdefault("stream_methods", []).append(
                    (fn, method_name, ret_type)
                )

    if "stream_methods" in stream_base_types:
        entries = stream_base_types["stream_methods"]
        if len(entries) >= 2:
            types_seen = {entry[2] for entry in entries}
            _WRAPPER_TYPES = ("StreamingResponse", "EventSourceResponse")
            normalized = set()
            for t in types_seen:
                if any(w in t for w in _WRAPPER_TYPES):
                    continue
                elif "Iterator" in t or "Generator" in t:
                    normalized.add("iterator_family")
                elif "AsyncIterator" in t or "AsyncGenerator" in t:
                    normalized.add("async_iterator_family")
                else:
                    normalized.add(t)
            if len(normalized) > 1:
                results.append(
                    ConsistencyResult(
                        check="type_agreement",
                        passed=False,
                        detail=(
                            "Streaming methods have incompatible return types: "
                            + ", ".join(f"{fn}:{method} -> {ret}" for fn, method, ret in entries)
                        ),
                    )
                )
            else:
                results.append(
                    ConsistencyResult(
                        check="type_agreement",
                        passed=True,
                        detail="Streaming return types are compatible",
                    )
                )

    seen: dict[str, int] = {}
    for art in artifacts:
        fn = _normalize_artifact_filename(art.get("filename", ""))
        content_hash = hash(art.get("content", "").strip())
        if fn in seen and seen[fn] == content_hash:
            results.append(
                ConsistencyResult(
                    check="no_duplicates",
                    passed=False,
                    detail=f"Duplicate artifact: {fn} appears with identical content",
                )
            )
        elif fn in seen:
            results.append(
                ConsistencyResult(
                    check="no_duplicates",
                    passed=False,
                    detail=f"Duplicate artifact: {fn} appears with DIFFERENT content (ambiguous)",
                )
            )
        seen[fn] = content_hash

    if not any(c.check == "no_duplicates" for c in results):
        results.append(
            ConsistencyResult(
                check="no_duplicates",
                passed=True,
                detail="No duplicate artifacts",
            )
        )

    if not any(c.check == "type_agreement" for c in results):
        results.append(
            ConsistencyResult(
                check="type_agreement",
                passed=True,
                detail="No streaming type conflicts detected",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Live score wrapper (0-70 scale)
# ---------------------------------------------------------------------------


@dataclass
class LiveScore:
    """Result of live scoring a plan.

    - artifact_quality: 0-50 (mean of per-artifact scores * 0.5)
    - consistency: 0-20 (ratio of passed consistency checks * 20)
    - total: sum of above, 0-70
    - artifact_count: for visibility
    - applicable: False if the plan has no artifacts (short-circuit case)
    """

    artifact_quality: float = 0.0
    consistency: float = 0.0
    total: float = 0.0
    artifact_count: int = 0
    applicable: bool = True
    artifact_checks: list[ArtifactCheck] = field(default_factory=list)
    consistency_checks: list[ConsistencyResult] = field(default_factory=list)


def score_plan_live(
    plan_data: dict[str, Any],
    structural_index: str = "",
    source_dir: str = "",
    task_requires_streaming: bool = True,
) -> LiveScore:
    """Compute the live quality score (artifact_quality + consistency, 0-70).

    Live scoring skips the completeness dimension (requires a taxonomy).
    Never raises — returns LiveScore(applicable=False) when the plan has
    no artifacts (e.g. short-circuit due to already-implemented).

    Args:
        plan_data: The pipeline output dict. Expected to contain
            `plan_data["design"]["artifacts"]` with `{filename, content}` dicts.
        structural_index: Optional markdown structural overview used by the
            grounding lookup. May be empty if `source_dir` is provided.
        source_dir: Optional path to the target codebase; when present the
            grounding lookup is augmented with a full-disk scan.
        task_requires_streaming: Toggle for the streaming-specific checks.

    Returns:
        LiveScore. `total` is 0-70. `applicable=False` when artifacts is empty.
    """
    design = plan_data.get("design") or {}
    artifacts = design.get("artifacts") or []
    artifact_dicts = [
        {"filename": a.get("filename", ""), "content": a.get("content", "")} for a in artifacts
    ]

    if not artifact_dicts:
        return LiveScore(applicable=False, artifact_count=0)

    artifact_checks = check_all_artifacts_v2(
        artifact_dicts,
        structural_index,
        task_requires_streaming=task_requires_streaming,
        source_dir=source_dir,
    )

    consistency_checks = check_cross_artifact_consistency(
        artifact_dicts,
        artifact_checks,
        structural_index=structural_index,
        source_dir=source_dir,
    )

    if artifact_checks:
        weights = [max(10, a.content_lines) for a in artifact_checks]
        total_weight = sum(weights)
        artifact_mean = sum(a.score * w for a, w in zip(artifact_checks, weights)) / total_weight
    else:
        artifact_mean = 0.0
    artifact_quality = round(artifact_mean * 0.5, 1)

    if consistency_checks:
        consistency_passed = sum(1 for c in consistency_checks if c.passed)
        consistency_ratio = consistency_passed / len(consistency_checks)
    else:
        consistency_ratio = 1.0
    consistency = round(consistency_ratio * 20, 1)

    return LiveScore(
        artifact_quality=artifact_quality,
        consistency=consistency,
        total=round(artifact_quality + consistency, 1),
        artifact_count=len(artifact_dicts),
        applicable=True,
        artifact_checks=list(artifact_checks),
        consistency_checks=list(consistency_checks),
    )
