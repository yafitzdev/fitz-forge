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
    artifacts, structural_index, task_requires_streaming=True, source_dir=""
):
    """Thin wrapper: library dataclasses -> benchmark Pydantic models."""
    results = _lib_check_all_artifacts_v2(
        artifacts, structural_index, task_requires_streaming, source_dir
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
        artifact_dicts, artifact_checks, structural_index, source_dir
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
