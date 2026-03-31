# benchmarks/eval_deterministic.py
"""
Deterministic plan scorer — zero variance, 100% reproducible.

Checks plan artifacts and structure against the real codebase using
AST parsing and structural index lookups. No LLM calls.

Produces a DeterministicScore with hard metrics:
  - fabrication_count: self.xxx references that don't exist
  - real_ref_count: self.xxx references that DO exist
  - fabrication_ratio: fabricated / total
  - file_accuracy: fraction of artifact filenames that exist on disk
  - syntax_errors: number of artifacts that fail ast.parse
  - phase_consistency: total_phases matches actual phase count, etc.
  - field_errors: request.xxx references to non-existent schema fields

Usage:
    from benchmarks.eval_deterministic import score_plan
    result = score_plan(plan_data, structural_index, source_dir)
    print(result)
"""

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ArtifactCheck:
    """Check results for a single artifact."""
    filename: str
    exists_on_disk: bool
    syntax_valid: bool
    real_refs: list[str] = field(default_factory=list)
    fabricated_refs: list[str] = field(default_factory=list)
    field_errors: list[str] = field(default_factory=list)


@dataclass
class DeterministicScore:
    """Deterministic plan quality metrics — zero scorer variance."""
    # Artifact grounding
    artifact_count: int = 0
    syntax_errors: int = 0
    file_accuracy: float = 0.0  # fraction of artifact files that exist
    total_real_refs: int = 0
    total_fabricated_refs: int = 0
    fabrication_ratio: float = 0.0  # fabricated / (real + fabricated)
    total_field_errors: int = 0
    # Coverage
    needed_artifact_count: int = 0
    covered_artifact_count: int = 0
    coverage_ratio: float = 0.0  # covered / needed
    avg_artifact_chars: float = 0.0  # average content length
    # Structural consistency
    phase_count_match: bool = True
    critical_path_valid: bool = True
    parallel_opps_valid: bool = True
    # Per-artifact detail
    artifacts: list[ArtifactCheck] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        """Composite score 0-100. Higher = better."""
        score = 100.0
        # Fabrication penalty: -5 per fabricated ref
        score -= self.total_fabricated_refs * 5
        # Syntax error penalty: -10 per broken artifact
        score -= self.syntax_errors * 10
        # File accuracy bonus/penalty
        score -= (1.0 - self.file_accuracy) * 20
        # Field error penalty: -3 per wrong field
        score -= self.total_field_errors * 3
        # Coverage penalty: -20 if less than 50% covered, scaled
        if self.needed_artifact_count > 0:
            score -= (1.0 - self.coverage_ratio) * 20
        # Phase consistency penalty
        if not self.phase_count_match:
            score -= 5
        if not self.critical_path_valid:
            score -= 5
        if not self.parallel_opps_valid:
            score -= 5
        return max(0.0, min(100.0, score))

    def summary(self) -> str:
        lines = [
            f"Deterministic Score: {self.total_score:.0f}/100",
            f"  Artifacts: {self.artifact_count} "
            f"({self.syntax_errors} syntax errors, "
            f"{self.file_accuracy:.0%} file accuracy, "
            f"avg {self.avg_artifact_chars:.0f} chars)",
            f"  Coverage: {self.covered_artifact_count}/{self.needed_artifact_count} "
            f"needed artifacts covered ({self.coverage_ratio:.0%})",
            f"  References: {self.total_real_refs} real, "
            f"{self.total_fabricated_refs} fabricated "
            f"({self.fabrication_ratio:.0%} fabrication rate)",
            f"  Field errors: {self.total_field_errors}",
            f"  Phase consistency: "
            f"count={'ok' if self.phase_count_match else 'MISMATCH'}, "
            f"critical_path={'ok' if self.critical_path_valid else 'INVALID'}, "
            f"parallel={'ok' if self.parallel_opps_valid else 'INVALID'}",
        ]
        if self.total_fabricated_refs > 0:
            lines.append("  Fabricated refs:")
            for a in self.artifacts:
                for ref in a.fabricated_refs:
                    lines.append(f"    {a.filename}: {ref}")
        if self.total_field_errors > 0:
            lines.append("  Field errors:")
            for a in self.artifacts:
                for err in a.field_errors:
                    lines.append(f"    {a.filename}: {err}")
        return "\n".join(lines)


# Known schema fields for common request types
_KNOWN_REQUEST_FIELDS = {
    "QueryRequest": {"question", "source", "top_k", "conversation_context"},
    "ChatRequest": {"message", "history", "collection", "source"},
    "ChatResponse": {"text", "mode", "sources"},
    "QueryResponse": {"text", "mode", "sources", "provenance"},
}

# Skip these — builtins, framework, stdlib
_SKIP_SELF_REFS = frozenset({
    "__class__", "__dict__", "__init__", "__repr__", "__str__",
    "__enter__", "__exit__", "__aenter__", "__aexit__",
    "__call__", "__iter__", "__next__", "__aiter__", "__anext__",
})


def _dedent(text: str) -> str:
    """Remove common leading whitespace from all lines."""
    import textwrap
    return textwrap.dedent(text)


def _indent(text: str, prefix: str = "    ") -> str:
    """Add prefix to each non-empty line."""
    lines = text.split("\n")
    return "\n".join(prefix + line if line.strip() else line for line in lines)


def score_plan(
    plan_data: dict,
    structural_index: str,
    source_dir: str | Path,
) -> DeterministicScore:
    """Score a plan deterministically against the real codebase."""
    from fitz_forge.planning.validation.grounding import StructuralIndexLookup

    lookup = StructuralIndexLookup(structural_index)
    source_dir = Path(source_dir)
    result = DeterministicScore()

    # 1. Check artifacts
    artifacts = plan_data.get("design", {}).get("artifacts", [])
    result.artifact_count = len(artifacts)

    files_exist = 0
    for art in artifacts:
        check = _check_artifact(art, lookup, source_dir)
        result.artifacts.append(check)
        if check.exists_on_disk:
            files_exist += 1
        if not check.syntax_valid:
            result.syntax_errors += 1
        result.total_real_refs += len(check.real_refs)
        result.total_fabricated_refs += len(check.fabricated_refs)
        result.total_field_errors += len(check.field_errors)

    if artifacts:
        result.file_accuracy = files_exist / len(artifacts)
        content_lengths = [len(a.get("content", "")) for a in artifacts]
        result.avg_artifact_chars = sum(content_lengths) / len(artifacts)

    total_refs = result.total_real_refs + result.total_fabricated_refs
    if total_refs > 0:
        result.fabrication_ratio = result.total_fabricated_refs / total_refs

    # 2. Check coverage: how many needed_artifacts have matching artifacts?
    needed = plan_data.get("context", {}).get("needed_artifacts", [])
    result.needed_artifact_count = len(needed)
    if needed:
        artifact_files = {a.get("filename", "") for a in artifacts}
        covered = 0
        for entry in needed:
            # Format: "path/to/file.py -- purpose" or just "path/to/file.py"
            fname = entry.split(" -- ")[0].strip() if " -- " in entry else entry.strip()
            # Check if any artifact covers this file (partial match on basename)
            basename = fname.split("/")[-1]
            if any(fname in af or basename in af for af in artifact_files):
                covered += 1
        result.covered_artifact_count = covered
        result.coverage_ratio = covered / len(needed) if needed else 0.0

    # 3. Check structural consistency
    roadmap = plan_data.get("roadmap", {})
    _check_roadmap(roadmap, result)

    return result


def _check_artifact(
    artifact: dict,
    lookup: "StructuralIndexLookup",
    source_dir: Path,
) -> ArtifactCheck:
    """Check a single artifact against the codebase."""
    filename = artifact.get("filename", "")
    content = artifact.get("content", "")

    # Check file exists on disk
    exists = (source_dir / filename).is_file() if filename else False

    check = ArtifactCheck(filename=filename, exists_on_disk=exists, syntax_valid=True)

    if not content.strip():
        return check

    # Parse Python — artifacts are often code fragments (indented methods
    # meant to be added inside a class). Try parsing as-is first, then
    # dedented, then wrapped in a dummy class.
    tree = None
    for attempt in [content, _dedent(content), f"class _:\n{_indent(content)}"]:
        try:
            tree = ast.parse(attempt)
            break
        except SyntaxError:
            continue

    if tree is None:
        check.syntax_valid = False
        return check

    # Find target file's classes in the index
    target_classes = []
    for cls_list in lookup.classes.values():
        for cls in cls_list:
            if cls.file == filename or filename.endswith(cls.file):
                target_classes.append(cls)

    # Extract real init attrs from source on disk
    real_attrs = _extract_init_attrs(filename, source_dir)

    # Collect all method names from target classes
    real_methods = set()
    for cls in target_classes:
        real_methods.update(cls.methods.keys())

    # Also add methods defined in the artifact itself
    artifact_methods = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            artifact_methods.add(node.name)

    # Also extract methods that exist on disk for the target file
    # (the structural index may truncate long method lists)
    disk_methods = _extract_disk_methods(filename, source_dir)

    # Check self.xxx references
    seen = set()
    for node in ast.walk(tree):
        # self.method() calls
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"):
            ref = node.func.attr
            if ref in seen or ref in _SKIP_SELF_REFS:
                continue
            seen.add(ref)
            if (ref in real_methods
                    or ref in artifact_methods
                    or ref in disk_methods
                    or lookup.method_exists_anywhere(ref)):
                check.real_refs.append(f"self.{ref}()")
            else:
                check.fabricated_refs.append(f"self.{ref}()")

        # self._attr references (not calls)
        elif (isinstance(node, ast.Attribute)
              and isinstance(node.value, ast.Name)
              and node.value.id == "self"
              and not isinstance(node.ctx, ast.Store)):
            ref = node.attr
            if ref in seen or ref in _SKIP_SELF_REFS:
                continue
            # Only check private attrs (self._xxx) — public might be properties
            if not ref.startswith("_"):
                continue
            seen.add(ref)
            if (ref in real_attrs or ref in real_methods
                    or ref in artifact_methods or ref in disk_methods):
                check.real_refs.append(f"self.{ref}")
            elif lookup.method_exists_anywhere(ref):
                check.real_refs.append(f"self.{ref}")
            else:
                check.fabricated_refs.append(f"self.{ref}")

        # request.field references
        elif (isinstance(node, ast.Attribute)
              and isinstance(node.value, ast.Name)
              and node.value.id == "request"):
            field_name = node.attr
            if field_name in seen:
                continue
            seen.add(f"request.{field_name}")
            # Check against known schemas
            is_valid = False
            for schema_name, fields in _KNOWN_REQUEST_FIELDS.items():
                if field_name in fields:
                    is_valid = True
                    break
            if not is_valid and field_name not in ("app", "state", "url", "method", "headers"):
                check.field_errors.append(
                    f"request.{field_name} — not a known field on any request schema"
                )

    return check


def _extract_init_attrs(filename: str, source_dir: Path) -> set[str]:
    """Extract self.xxx attribute names from __init__/_init_components on disk."""
    attrs = set()
    filepath = source_dir / filename
    if not filepath.is_file():
        # Try matching by basename
        basename = filename.split("/")[-1]
        for py in source_dir.rglob(basename):
            if ".venv" not in str(py) and "__pycache__" not in str(py):
                filepath = py
                break
        else:
            return attrs

    try:
        src = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return attrs

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for method in ast.iter_child_nodes(node):
                if (isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and method.name in ("__init__", "_init_components",
                                            "setup", "_setup")):
                    for n in ast.walk(method):
                        if (isinstance(n, ast.Assign)
                                and n.targets
                                and isinstance(n.targets[0], ast.Attribute)
                                and isinstance(n.targets[0].value, ast.Name)
                                and n.targets[0].value.id == "self"):
                            attrs.add(n.targets[0].attr)
                        # Also catch annotated assignments: self.x: Type = ...
                        elif (isinstance(n, ast.AnnAssign)
                              and isinstance(n.target, ast.Attribute)
                              and isinstance(n.target.value, ast.Name)
                              and n.target.value.id == "self"):
                            attrs.add(n.target.attr)

    return attrs


def _extract_disk_methods(filename: str, source_dir: Path) -> set[str]:
    """Extract all method names from classes in a file on disk."""
    methods = set()
    filepath = source_dir / filename
    if not filepath.is_file():
        basename = filename.split("/")[-1]
        for py in source_dir.rglob(basename):
            if ".venv" not in str(py) and "__pycache__" not in str(py):
                filepath = py
                break
        else:
            return methods

    try:
        src = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return methods

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(child.name)

    return methods


def _check_roadmap(roadmap: dict, result: DeterministicScore) -> None:
    """Check roadmap structural consistency."""
    phases = roadmap.get("phases", [])
    total_phases = roadmap.get("total_phases", len(phases))
    critical_path = roadmap.get("critical_path", [])
    parallel_opps = roadmap.get("parallel_opportunities", [])

    # Phase count match
    if total_phases != len(phases):
        result.phase_count_match = False

    # Critical path references valid phases
    phase_numbers = {p.get("number", i + 1) for i, p in enumerate(phases)}
    if critical_path:
        for p in critical_path:
            if p not in phase_numbers:
                result.critical_path_valid = False
                break

    # Parallel opportunities reference valid phases
    if parallel_opps:
        for group in parallel_opps:
            if isinstance(group, list):
                for p in group:
                    if p not in phase_numbers:
                        result.parallel_opps_valid = False
                        break
            elif isinstance(group, int):
                if group not in phase_numbers:
                    result.parallel_opps_valid = False


def score_plan_file(
    plan_path: str | Path,
    structural_index: str,
    source_dir: str | Path,
) -> DeterministicScore:
    """Score a plan from a JSON file path."""
    plan_data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    return score_plan(plan_data, structural_index, source_dir)


def score_batch(
    plan_dir: str | Path,
    structural_index: str,
    source_dir: str | Path,
) -> list[tuple[str, DeterministicScore]]:
    """Score all plans in a directory."""
    plan_dir = Path(plan_dir)
    results = []
    for plan_file in sorted(plan_dir.glob("plan_*.json")):
        score = score_plan_file(plan_file, structural_index, source_dir)
        results.append((plan_file.name, score))
    return results
