# benchmarks/eval_v2_deterministic.py
"""
Tier 1: Deterministic checks for Scorer V2.

Zero LLM cost. Produces a reproducible score from:
  1. Completeness — are required files present in the plan?
  2. Per-artifact AST validation — syntax, fabrications, behavioral checks
  3. Cross-artifact consistency — do artifacts agree on names/types?

The per-artifact AST helpers and cross-artifact consistency helpers live in
`fitz_forge.planning.validation.scoring` so the CLI can import them without
depending on this benchmarks package. This module keeps the completeness
check (bench-only; requires a taxonomy) and the combined Tier 1 report that
re-packages library results into the benchmark's Pydantic schemas.
"""

import logging
import re
from collections import Counter

from fitz_forge.planning.validation.scoring import (
    ArtifactCheck as LibArtifactCheck,
)
from fitz_forge.planning.validation.scoring import (
    ConsistencyResult as LibConsistencyResult,
)
from fitz_forge.planning.validation.scoring import (
    check_all_artifacts_v2 as _lib_check_all_artifacts_v2,
)
from fitz_forge.planning.validation.scoring import (
    check_cross_artifact_consistency as _lib_check_cross_artifact_consistency,
)
from fitz_forge.planning.validation.scoring import (
    check_single_artifact as _lib_check_single_artifact,
)

from .eval_v2_schemas import (
    ArtifactCheck,
    CompletenessResult,
    ConsistencyResult,
    DeterministicReport,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Benchmark-schema adapters (library dataclass -> Pydantic BaseModel)
# ---------------------------------------------------------------------------


def _adapt_artifact_check(lib: LibArtifactCheck) -> ArtifactCheck:
    return ArtifactCheck(**lib.__dict__)


def _adapt_consistency_result(lib: LibConsistencyResult) -> ConsistencyResult:
    return ConsistencyResult(check=lib.check, passed=lib.passed, detail=lib.detail)


def check_single_artifact(artifact, lookup, task_requires_streaming=True):
    """Thin wrapper: library dataclass -> benchmark Pydantic model."""
    return _adapt_artifact_check(
        _lib_check_single_artifact(artifact, lookup, task_requires_streaming)
    )


def check_all_artifacts_v2(
    artifacts,
    structural_index,
    task_requires_streaming=True,
    source_dir="",
    augment_from=None,
):
    """Thin wrapper: library dataclasses -> benchmark Pydantic models."""
    results = _lib_check_all_artifacts_v2(
        artifacts,
        structural_index,
        task_requires_streaming,
        source_dir,
        augment_from=augment_from,
    )
    return [_adapt_artifact_check(r) for r in results]


def check_cross_artifact_consistency(
    artifacts, artifact_checks=None, structural_index="", source_dir=""
):
    """Thin wrapper: library dataclasses -> benchmark Pydantic models.

    Accepts either benchmark ArtifactCheck or library LibArtifactCheck inputs
    for artifact_checks; only `parseable` and `filename` are consulted.
    """
    # The library function only reads .parseable/.filename — both schemas have
    # the same attributes so we can pass through.
    results = _lib_check_cross_artifact_consistency(
        artifacts, artifact_checks, structural_index, source_dir
    )
    return [_adapt_consistency_result(r) for r in results]


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
# Combined Tier 1 score
# ---------------------------------------------------------------------------


def run_deterministic_checks(
    plan_data: dict,
    structural_index: str,
    task_requires_streaming: bool = True,
    taxonomy_files: dict[str, str] | None = None,
    source_dir: str = "",
    evaluated_filenames: set[str] | None = None,
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

    # Filter to "evaluated" artifacts — files that the taxonomy
    # explicitly scores (file_taxonomies keys). This keeps Craft and
    # Groundedness comparable across arms that produce different
    # scopes: one arm may proactively write tests, another may not; the
    # scorer should evaluate each arm on the SAME files rather than
    # penalise an arm for writing extras. Without an evaluated-filename
    # set, falls back to all artifacts (original behaviour).
    if evaluated_filenames:
        artifact_dicts_evaluated = [
            a for a in artifact_dicts
            if _matches_evaluated(a["filename"], evaluated_filenames)
        ]
    else:
        artifact_dicts_evaluated = artifact_dicts

    # 1. Completeness — uses the full artifact set (file-presence across
    #    everything the plan shipped, incl. extras).
    completeness = check_completeness(plan_data, decisions, taxonomy_files)

    # 2. Per-artifact checks — on the evaluated subset only, but the
    #    fabrication-detector's lookup is enriched with defs from the
    #    FULL artifact set so sibling-artifact cross-references (e.g. a
    #    StreamEvent class defined by answer.py and used from engine.py)
    #    aren't wrongly flagged as fabrications.
    artifact_checks = check_all_artifacts_v2(
        artifact_dicts_evaluated,
        structural_index,
        task_requires_streaming,
        source_dir,
        augment_from=artifact_dicts,
    )

    # 3. Cross-artifact consistency — on the evaluated subset.
    consistency_checks = check_cross_artifact_consistency(
        artifact_dicts_evaluated, artifact_checks, structural_index, source_dir
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

    # Coverage + Craft split — see DeterministicReport for the motivation.
    # Strict coverage treats a required file that shipped as a
    # NotImplementedError stub as uncovered, so a 'raise NotImplementedError'
    # engine.py doesn't silently count against the harness.
    coverage_strict = _compute_strict_coverage(completeness, artifact_checks)
    # Craft = normalized average of artifact_quality (/50) and consistency (/20),
    # so both Craft and Coverage live on the same 0-100 scale.
    craft = round(
        (artifact_mean + consistency_ratio * 100) / 2,
        1,
    )
    # Groundedness — full-codebase grounding check on the evaluated
    # artifact set, but the lookup is enriched with defs from the FULL
    # artifact set so sibling artifacts outside the filter still count
    # (a plan can legitimately define ``StreamEvent`` in ``schemas.py``
    # and reference it from ``engine.py``; filtering to "evaluated"
    # files shouldn't make the reference look fabricated).
    groundedness, grounding_violations = _compute_groundedness(
        artifact_dicts_evaluated,
        structural_index,
        source_dir,
        augment_from=artifact_dicts,
    )
    # Actionability — can an agent execute this plan end-to-end?
    # Phases with a real verification_command can be closed-loop tested.
    actionability = _compute_actionability(plan_data)

    return DeterministicReport(
        completeness=completeness,
        artifact_checks=artifact_checks,
        consistency_checks=consistency_checks,
        completeness_score=completeness_score,
        artifact_quality_score=artifact_quality_score,
        consistency_score=consistency_score,
        deterministic_score=deterministic_score,
        coverage_strict=coverage_strict,
        craft=craft,
        groundedness=groundedness,
        grounding_violations=grounding_violations,
        actionability=actionability,
    )


def _matches_evaluated(filename: str, evaluated: set[str]) -> bool:
    """Match an artifact filename against the taxonomy's evaluated-file keys.

    Taxonomies list files as short names like ``engine.py`` or
    ``routes/query.py``; artifact filenames are full repo-relative
    paths like ``fitz_sage/engines/fitz_krag/engine.py``. Match on:

    * Exact string equality.
    * Artifact's path ends with ``"/" + key`` (e.g.
      ``fitz_sage/.../engine.py`` ends with ``/engine.py``).
    * Key ends with ``"/" + artifact_basename`` (symmetric, in case
      the taxonomy uses a longer suffix).

    Everything else — tests, extras, files the taxonomy didn't
    declare — is treated as "not evaluated" and filtered out.
    """
    if not filename:
        return False
    fn = filename.replace("\\", "/").strip("/")
    basename = fn.split("/")[-1]
    for key in evaluated:
        k = key.replace("\\", "/").strip("/")
        if fn == k:
            return True
        if fn.endswith("/" + k):
            return True
        if k == basename:
            return True
        if k.endswith("/" + basename):
            return True
    return False


def _compute_groundedness(
    artifact_dicts: list[dict],
    structural_index: str,
    source_dir: str,
    augment_from: list[dict] | None = None,
) -> tuple[float, int]:
    """Fraction of artifacts free of grounding violations against the real codebase.

    Runs the same ``check_all_artifacts`` pass the pipeline uses to gate
    artifact emission. Returns ``(score, total_violations)``. Score is
    100 when no artifact has any violation, 0 when every artifact has
    at least one. An empty artifact set scores 100 (nothing to ground).

    ``augment_from`` forwards to ``check_all_artifacts``'s lookup-
    augmentation hook: when scoring an evaluated subset, passing the
    full artifact set here preserves sibling-artifact cross-references.
    """
    if not artifact_dicts:
        return 100.0, 0
    from fitz_forge.planning.validation.grounding.check import check_all_artifacts

    violations = check_all_artifacts(
        artifact_dicts,
        structural_index,
        source_dir=source_dir,
        augment_from=augment_from,
    )
    dirty_files = {v.artifact for v in violations}
    clean = len(artifact_dicts) - len(dirty_files)
    score = round(clean / len(artifact_dicts) * 100, 1)
    return score, len(violations)


_ACTIONABILITY_PLACEHOLDER_RE = re.compile(
    r"^\s*(todo|tbd|write tests|run tests|verify manually|manual verification)\s*$",
    re.IGNORECASE,
)


def _compute_actionability(plan_data: dict) -> float:
    """Fraction of roadmap phases that carry a concrete verification command.

    A phase is actionable if its ``verification_command`` field is non-
    empty, non-whitespace, and isn't one of a few placeholder phrases
    that mean "figure it out yourself." Plans with no phases score 0 —
    you can't execute a plan that doesn't describe what to do.
    """
    phases = plan_data.get("roadmap", {}).get("phases", [])
    if not phases:
        return 0.0

    actionable = 0
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        cmd = str(phase.get("verification_command") or "").strip()
        if not cmd:
            continue
        if _ACTIONABILITY_PLACEHOLDER_RE.match(cmd):
            continue
        actionable += 1
    return round(actionable / len(phases) * 100, 1)


def _compute_strict_coverage(completeness, artifact_checks) -> float:
    """Fraction of required files present AND not a NotImplementedError stub.

    The existing completeness.score gives partial credit for files that
    exist in the plan but doesn't know they may be stubs. A required
    engine.py that ships as ``raise NotImplementedError`` with helpful
    comments is classified 'present' by file-path matching even though
    it delivers nothing. Strict coverage treats such stubs as uncovered.
    """
    required_files = completeness.required_files
    present_required = set(completeness.present_required)
    if not required_files:
        return 100.0

    ac_by_name: dict[str, object] = {}
    for ac in artifact_checks:
        ac_by_name[ac.filename] = ac
        tail = ac.filename.split("/")[-1]
        ac_by_name[tail] = ac

    covered = 0
    for req in required_files:
        if req not in present_required:
            continue
        # Find the matching artifact check — may live under a deeper path
        # in the plan (e.g. ``fitz_sage/engines/fitz_krag/engine.py`` vs
        # the short ``engine.py`` listed in the taxonomy).
        ac = ac_by_name.get(req)
        if ac is None:
            for fname, candidate in ac_by_name.items():
                if fname.endswith("/" + req) or req.endswith("/" + fname):
                    ac = candidate
                    break
        if ac is None:
            covered += 1
            continue
        if getattr(ac, "has_not_implemented", False):
            continue
        covered += 1
    return round(covered / len(required_files) * 100, 1)
