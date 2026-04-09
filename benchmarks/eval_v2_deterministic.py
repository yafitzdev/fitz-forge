# benchmarks/eval_v2_deterministic.py
"""
Tier 1: Deterministic checks for Scorer V2.

Zero LLM cost. Produces a reproducible score from:
  1. Completeness — are required files present in the plan?
  2. Per-artifact AST validation — syntax, fabrications, behavioral checks
  3. Cross-artifact consistency — do artifacts agree on names/types?

Reuses StructuralIndexLookup and check_artifact from grounding.py.
"""

import ast
import logging
import re
import textwrap
from collections import Counter

from fitz_forge.planning.validation.grounding import (
    StructuralIndexLookup,
    Violation,
    check_artifact,
)

from .eval_v2_schemas import (
    ArtifactCheck,
    CompletenessResult,
    ConsistencyResult,
    DeterministicReport,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Completeness check
# ---------------------------------------------------------------------------


def _compute_file_reference_counts(
    decisions: list[dict],
) -> Counter:
    """Count how many decisions reference each file."""
    counts: Counter = Counter()
    for dec in decisions:
        for f in dec.get("relevant_files", []):
            # Normalize path
            counts[f.replace("\\", "/")] += 1
    return counts


def _normalize_artifact_filename(filename: str) -> str:
    """Normalize an artifact filename for comparison."""
    return filename.replace("\\", "/").strip("/")


def check_completeness(
    plan_data: dict,
    decisions: list[dict],
    taxonomy_files: dict[str, str] | None = None,
) -> CompletenessResult:
    """Check which required/recommended/optional files are present in artifacts.

    If taxonomy_files is provided (from streaming_taxonomy.json file_taxonomies),
    those file patterns define the required set. Decision-derived counts become
    a fallback for tasks without a taxonomy.

    taxonomy_files: dict mapping file pattern (e.g. "engine.py") to tier
                    ("required", "recommended", "optional").
    """
    # Get artifact filenames from plan
    artifacts = plan_data.get("design", {}).get("artifacts", [])
    artifact_files = {_normalize_artifact_filename(a.get("filename", "")) for a in artifacts}

    def _file_present(target: str) -> bool:
        """Check if a required file is covered by any artifact (suffix match)."""
        target_norm = target.replace("\\", "/")
        for af in artifact_files:
            if af == target_norm or af.endswith(target_norm) or target_norm.endswith(af):
                return True
        return False

    if taxonomy_files:
        # Use taxonomy-defined required files
        required = [f for f, tier in taxonomy_files.items() if tier == "required"]
        recommended = [f for f, tier in taxonomy_files.items() if tier == "recommended"]
        optional = [f for f, tier in taxonomy_files.items() if tier == "optional"]
    else:
        # Derive from decision file references
        ref_counts = _compute_file_reference_counts(decisions)
        required = []
        recommended = []
        optional = []
        for filepath, count in ref_counts.items():
            if count >= 3:
                required.append(filepath)
            elif count == 2:
                recommended.append(filepath)
            else:
                optional.append(filepath)

    present_req = [f for f in required if _file_present(f)]
    present_rec = [f for f in recommended if _file_present(f)]
    present_opt = [f for f in optional if _file_present(f)]
    missing_req = [f for f in required if not _file_present(f)]

    req_ratio = len(present_req) / len(required) if required else 1.0

    # Score: base ratio + bonuses for recommended/optional
    rec_bonus = (len(present_rec) / len(recommended) * 0.15) if recommended else 0.0
    opt_bonus = (len(present_opt) / len(optional) * 0.05) if optional else 0.0
    score = min(1.0, req_ratio + rec_bonus + opt_bonus)

    return CompletenessResult(
        required_files=sorted(required),
        recommended_files=sorted(recommended),
        optional_files=sorted(optional),
        present_required=sorted(present_req),
        present_recommended=sorted(present_rec),
        present_optional=sorted(present_opt),
        missing_required=sorted(missing_req),
        required_ratio=round(req_ratio, 3),
        score=round(score, 3),
    )


# ---------------------------------------------------------------------------
# 2. Per-artifact AST validation
# ---------------------------------------------------------------------------

# Patterns that indicate streaming behavior
_YIELD_RE = re.compile(r"\byield\b")
_NOT_IMPLEMENTED_RE = re.compile(r"\bNotImplementedError\b")
_SYS_STDOUT_RE = re.compile(r"\bsys\.stdout\b")

# Files where we expect yield for streaming tasks
_STREAMING_INDICATORS = ("engine.py", "synthesizer.py")


def _count_violations_by_kind(violations: list[Violation], kind: str) -> int:
    return sum(1 for v in violations if v.kind == kind)


# Patterns for regex-based fabrication detection on unparseable code
_SELF_METHOD_RE = re.compile(r"self\.([a-zA-Z_]\w*)\s*\(")
_CLASS_CTOR_RE = re.compile(r"\b([A-Z][a-zA-Z0-9_]*)\s*\(")
# Names that look like classes but aren't (builtins, typing, common frameworks)
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


def _strip_comments(content: str) -> str:
    """Remove Python comments and docstrings from content for regex scanning."""
    lines = []
    in_docstring = False
    docstring_char = ""
    for line in content.split("\n"):
        stripped = line.strip()
        # Track docstrings
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_char = stripped[:3]
                if stripped.count(docstring_char) >= 2 and len(stripped) > 3:
                    continue  # single-line docstring
                in_docstring = True
                continue
        else:
            if docstring_char in stripped:
                in_docstring = False
            continue
        # Strip inline comments
        if "#" in line:
            # Naive but sufficient: split on # not inside strings
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
    Less precise than AST-based detection but catches obvious fabrications
    in unparseable code rather than reporting 0.
    """
    # Strip comments first — capitalized words in comments (e.g.,
    # "# Step 2: Batch(Analysis)") are not fabricated classes.
    code_only = _strip_comments(content)

    fab_self = 0
    fab_class = 0

    # Build set of locally defined names (functions and classes in the artifact)
    local_defs: set[str] = set()
    for line in code_only.split("\n"):
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            # Extract function name
            name = stripped.split("def ", 1)[1].split("(")[0].strip()
            local_defs.add(name)
        elif stripped.startswith("class "):
            name = stripped.split("class ", 1)[1].split("(")[0].split(":")[0].strip()
            local_defs.add(name)

    # Check self.method() calls
    for m in _SELF_METHOD_RE.finditer(code_only):
        method_name = m.group(1)
        if method_name.startswith("__"):
            continue
        if method_name in local_defs:
            continue  # defined in this artifact
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

    # Check ClassName() constructors
    for m in _CLASS_CTOR_RE.finditer(code_only):
        class_name = m.group(1)
        if class_name in _CLASS_SKIP:
            continue
        if class_name in local_defs:
            continue  # defined in this artifact
        if not lookup.class_exists(class_name):
            fab_class += 1

    return fab_self, 0, 0, fab_class


def _try_parse(content: str) -> tuple[ast.Module | None, str | None]:
    """Try to parse content as Python, with recovery for code fragments.

    Surgical rewrite artifacts are often method bodies indented at class
    level (4 spaces).  These aren't standalone-parseable but ARE valid
    Python once dedented or wrapped.

    Returns (tree, recovered_content) — recovered_content is the version
    that parsed, so grounding checks can re-run on it if different from
    original.
    """
    # 1. Try as-is
    try:
        return ast.parse(content), None
    except SyntaxError:
        pass

    # 2. Try dedent (handles indented method bodies)
    dedented = textwrap.dedent(content)
    if dedented != content:
        try:
            return ast.parse(dedented), dedented
        except SyntaxError:
            pass

    # 3. Try wrapping in a class (for self-referencing methods)
    try:
        wrapped = "class _Wrapper:\n" + content
        return ast.parse(wrapped), wrapped
    except SyntaxError:
        pass

    # 4. Try dedent + class wrap
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
    """Run all deterministic checks on a single artifact."""
    filename = artifact.get("filename", "unknown")
    content = artifact.get("content", "")

    # Try to parse with recovery for code fragments
    tree, recovered_content = _try_parse(content)
    parseable = tree is not None

    # If we recovered the content, re-run grounding checks on the
    # parseable version so fabrication detection actually works
    if recovered_content is not None:
        violations = check_artifact({"filename": filename, "content": recovered_content}, lookup)
    else:
        violations = check_artifact(artifact, lookup)

    has_yield = None
    has_correct_return_type = None

    # Check for yield (only relevant for streaming-related files)
    is_streaming_file = any(filename.endswith(ind) for ind in _STREAMING_INDICATORS)
    if task_requires_streaming and is_streaming_file and tree is not None:
        has_yield = bool(_YIELD_RE.search(content))

    # Check return type annotations on main functions
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check streaming methods
                if is_streaming_file and "stream" in node.name.lower():
                    if node.returns:
                        ret_str = ast.dump(node.returns)
                        # Should be Iterator/Generator/AsyncGenerator, not Answer/str
                        good_returns = ("Iterator", "Generator", "AsyncGenerator", "AsyncIterator")
                        bad_returns = ("Answer", "str", "dict")
                        if any(r in ret_str for r in good_returns):
                            has_correct_return_type = True
                        elif any(r in ret_str for r in bad_returns):
                            has_correct_return_type = False

    # String-based checks
    has_not_implemented = bool(_NOT_IMPLEMENTED_RE.search(content))
    has_sys_stdout = bool(_SYS_STDOUT_RE.search(content))

    # Count violation types from AST-based check
    fab_self = _count_violations_by_kind(violations, "missing_method")
    fab_field = _count_violations_by_kind(violations, "wrong_field")
    fab_class = _count_violations_by_kind(violations, "missing_class")
    fab_chained = sum(
        1
        for v in violations
        if v.kind == "missing_method" and "." in v.symbol and v.symbol.count(".") >= 2
    )
    fab_self -= fab_chained  # Don't double-count

    # If AST check couldn't run (parse failure returned only a parse_error
    # violation), fall back to regex-based detection so fabrications aren't
    # hidden behind parse failures.
    if not parseable and len(violations) <= 1:
        r_self, r_chain, r_field, r_class = _regex_fabrication_scan(content, lookup)
        fab_self = max(fab_self, r_self)
        fab_chained = max(fab_chained, r_chain)
        fab_field = max(fab_field, r_field)
        fab_class = max(fab_class, r_class)

    # Weighted scoring: each check contributes a value 0.0-1.0.
    #
    # Weights:
    #   parseable (10%) — can't use it if it doesn't parse
    #   fabrication (50%) — SINGLE combined count across all types
    #   behavioral (20%) — yield, return type
    #   hygiene (20%) — NotImplementedError, sys.stdout
    weighted_checks: list[tuple[str, float, float]] = []  # (name, value 0-1, weight)

    weighted_checks.append(("parseable", 1.0 if parseable else 0.0, 10.0))

    # Combined fabrication score — total violations across all types.
    # 0→1.0, 1→0.7, 2→0.4, 3→0.2, 4+→0.0
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

    # Always include streaming checks to keep denominator consistent.
    # Non-streaming files pass by default (not applicable = pass).
    yield_val = 1.0
    if has_yield is not None:
        yield_val = 1.0 if has_yield else 0.0
    weighted_checks.append(("has_yield", yield_val, 10.0))

    ret_val = 1.0
    if has_correct_return_type is not None:
        ret_val = 1.0 if has_correct_return_type else 0.0
    weighted_checks.append(("correct_return_type", ret_val, 10.0))

    # Compute score
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
    """Run per-artifact checks on all artifacts.

    If source_dir is provided, augments the structural index with a full
    codebase scan so classes/functions outside the retrieval subset are
    recognized (not flagged as fabricated).
    """
    lookup = StructuralIndexLookup(structural_index)
    if source_dir:
        added = lookup.augment_from_source_dir(source_dir)
        if added:
            logger.info(f"Augmented index with {added} classes from {source_dir}")
    return [check_single_artifact(a, lookup, task_requires_streaming) for a in artifacts]


# ---------------------------------------------------------------------------
# 3. Cross-artifact consistency
# ---------------------------------------------------------------------------

# Pattern: method call like service.answer_stream() or engine.query_stream()
_METHOD_CALL_RE = re.compile(r"(?:self\.)?(\w+)\.(\w+)\s*\(")

# Pattern: return type annotation like -> Iterator[str]
_RETURN_TYPE_RE = re.compile(r"->\s*([A-Za-z_][\w\[\], ]*)")


def _extract_method_definitions(content: str) -> dict[str, str | None]:
    """Extract defined method names and their return types from artifact content.

    Uses the same parse recovery as _try_parse(): raw → dedent → class wrap.
    Without this, indented surgical rewrite artifacts (method bodies with
    4-space indent) silently return no methods, causing false consistency
    failures (V2-F6a).
    """
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


def check_cross_artifact_consistency(
    artifacts: list[dict],
    artifact_checks: list[ArtifactCheck] | None = None,
    structural_index: str = "",
) -> list[ConsistencyResult]:
    """Check consistency across artifacts.

    If artifact_checks is provided, unparseable artifacts are excluded
    from method-name agreement checks.  An unparseable artifact reports
    no methods, so any caller would fail — but that's a parse failure,
    not a consistency error.  Don't double-count.

    If structural_index is provided, method calls to methods that exist
    in the real codebase are skipped — they're not consistency errors,
    they're calls to existing code that the artifact doesn't redefine.
    """
    results: list[ConsistencyResult] = []

    # Build set of methods known in the real codebase (V2-F6a fix)
    codebase_methods: set[str] = set()
    if structural_index:
        from fitz_forge.planning.validation.grounding import StructuralIndexLookup

        lookup = StructuralIndexLookup(structural_index)
        codebase_methods = lookup._all_method_names | lookup._all_function_names

    # Track which files are unparseable (skip them as targets)
    unparseable: set[str] = set()
    if artifact_checks:
        for ac in artifact_checks:
            if not ac.parseable:
                unparseable.add(ac.filename)

    # Build maps: what methods each artifact defines and calls
    defined_methods: dict[str, dict[str, str | None]] = {}  # filename -> {method: ret_type}
    called_methods: dict[str, list[tuple[str, str]]] = {}  # filename -> [(obj, method)]

    for art in artifacts:
        fn = art.get("filename", "unknown")
        content = art.get("content", "")
        defined_methods[fn] = _extract_method_definitions(content)
        called_methods[fn] = _extract_method_calls(content)

    # Check 1: Method name agreement
    # If artifact A calls obj.some_method(), some artifact should define some_method()
    all_defined = set()
    for methods in defined_methods.values():
        all_defined.update(methods.keys())

    for caller_file, calls in called_methods.items():
        for obj_name, method_name in calls:
            # Skip common builtins/stdlib and private methods.
            # Private methods (_foo) are internal implementation details,
            # not part of the cross-artifact interface contract.
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
            ):
                continue
            # Check if any artifact defines this method
            if method_name not in all_defined:
                # Skip methods that exist in the real codebase — surgical
                # rewrites copy existing code that calls real methods.
                # These aren't consistency errors.
                if method_name in codebase_methods:
                    continue
                # Only flag if the object name matches another artifact's class/module.
                for target_file, target_methods in defined_methods.items():
                    if target_file == caller_file:
                        continue
                    # Skip unparseable targets — they report no methods
                    # but that's a parse failure, not a consistency error
                    if target_file in unparseable:
                        continue
                    # Check if object name relates to target file
                    # Strip leading underscores for comparison (self._synthesizer -> synthesizer)
                    target_basename = target_file.rsplit("/", 1)[-1].replace(".py", "")
                    obj_clean = obj_name.lstrip("_")
                    if obj_clean.lower() == target_basename.lower():
                        # This artifact seems to reference the target — method should exist
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

    # Check 2: Type agreement across artifacts
    # If one artifact returns Iterator[str], callers should handle Iterator[str]
    # (not AsyncGenerator or plain str)
    streaming_return_types: dict[str, str] = {}  # method_name -> return_type
    for _fn, methods in defined_methods.items():
        for method_name, ret_type in methods.items():
            if ret_type and "stream" in method_name.lower():
                streaming_return_types[method_name] = ret_type

    # Check if there are conflicting types for the same concept
    type_groups: dict[str, list[tuple[str, str]]] = {}  # base_name -> [(file, type)]
    for fn, methods in defined_methods.items():
        for method_name, ret_type in methods.items():
            if ret_type and "stream" in method_name.lower():
                # Group by base name (e.g., "answer_stream", "generate_stream")
                type_groups.setdefault(method_name, []).append((fn, ret_type))

    # Also check for type conflicts on similar method names
    # e.g., answer_stream in engine vs query_stream in service
    stream_base_types: dict[
        str, list[tuple[str, str, str]]
    ] = {}  # "stream" -> [(file, method, type)]
    for fn, methods in defined_methods.items():
        for method_name, ret_type in methods.items():
            if ret_type and "stream" in method_name.lower():
                stream_base_types.setdefault("stream_methods", []).append(
                    (fn, method_name, ret_type)
                )

    if "stream_methods" in stream_base_types:
        entries = stream_base_types["stream_methods"]
        if len(entries) >= 2:
            # Check if return types are compatible
            types_seen = {entry[2] for entry in entries}
            # Normalize type families:
            # - Iterator/Generator are the same family
            # - AsyncIterator/AsyncGenerator are the same family
            # - StreamingResponse/EventSourceResponse are wrappers (compatible with any)
            _WRAPPER_TYPES = ("StreamingResponse", "EventSourceResponse")
            normalized = set()
            for t in types_seen:
                if any(w in t for w in _WRAPPER_TYPES):
                    continue  # Wrappers are compatible with any streaming type
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

    # Check 3: No duplicate artifacts (same file, same content)
    seen: dict[str, str] = {}  # filename -> content hash
    for art in artifacts:
        fn = _normalize_artifact_filename(art.get("filename", ""))
        content = art.get("content", "").strip()
        if fn in seen and seen[fn] == hash(content):
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
        seen[fn] = hash(content)

    # If no duplicate check was added, add a passing one
    if not any(c.check == "no_duplicates" for c in results):
        results.append(
            ConsistencyResult(
                check="no_duplicates",
                passed=True,
                detail="No duplicate artifacts",
            )
        )

    # If no type agreement check was added, add a passing one
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
# Combined Tier 1 score
# ---------------------------------------------------------------------------


def run_deterministic_checks(
    plan_data: dict,
    structural_index: str,
    task_requires_streaming: bool = True,
    taxonomy_files: dict[str, str] | None = None,
    source_dir: str = "",
) -> DeterministicReport:
    """Run all Tier 1 checks and produce a scored report.

    Score formula (0-100):
      completeness * 30 + mean(artifact_scores) * 0.5 + consistency * 20

    taxonomy_files: dict mapping file pattern to tier ("required"/"recommended"/"optional").
                    When provided, overrides decision-derived completeness.
    source_dir: target codebase directory. When provided, augments the
                structural index with a full scan so classes outside the
                retrieval subset are recognized.
    """
    # Get decisions (from decomposed pipeline format)
    decisions = plan_data.get("decision_decomposition", {}).get("decisions", [])
    if not decisions:
        # Fall back to non-decomposed format
        decisions = plan_data.get("decisions", [])

    # Get artifacts
    artifacts = plan_data.get("design", {}).get("artifacts", [])
    artifact_dicts = [
        {"filename": a.get("filename", ""), "content": a.get("content", "")} for a in artifacts
    ]

    # 1. Completeness
    completeness = check_completeness(plan_data, decisions, taxonomy_files)

    # 2. Per-artifact checks
    artifact_checks = check_all_artifacts_v2(
        artifact_dicts, structural_index, task_requires_streaming, source_dir
    )

    # 3. Cross-artifact consistency
    consistency_checks = check_cross_artifact_consistency(
        artifact_dicts, artifact_checks, structural_index
    )

    # Score calculation
    completeness_score = round(completeness.score * 30, 1)

    if artifact_checks:
        # Size-weighted mean: larger artifacts carry more weight.
        # A 222-line engine.py with fabrications should dominate over
        # a 25-line service stub that trivially passes.
        # Minimum weight of 10 lines so tiny artifacts aren't ignored.
        weights = [max(10, a.content_lines) for a in artifact_checks]
        total_weight = sum(weights)
        artifact_mean = sum(a.score * w for a, w in zip(artifact_checks, weights)) / total_weight
    else:
        artifact_mean = 0.0
    artifact_quality_score = round(artifact_mean * 0.5, 1)

    if consistency_checks:
        consistency_passed = sum(1 for c in consistency_checks if c.passed)
        consistency_ratio = consistency_passed / len(consistency_checks)
    else:
        consistency_ratio = 1.0
    consistency_score = round(consistency_ratio * 20, 1)

    deterministic_score = round(completeness_score + artifact_quality_score + consistency_score, 1)

    return DeterministicReport(
        completeness=completeness,
        artifact_checks=artifact_checks,
        consistency_checks=consistency_checks,
        completeness_score=completeness_score,
        artifact_quality_score=artifact_quality_score,
        consistency_score=consistency_score,
        deterministic_score=deterministic_score,
    )
