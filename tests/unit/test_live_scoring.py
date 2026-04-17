# tests/unit/test_live_scoring.py
"""Unit tests for fitz_forge.planning.validation.scoring (live plan scoring)."""

from fitz_forge.planning.validation.scoring import (
    ArtifactCheck,
    ConsistencyResult,
    LiveScore,
    check_all_artifacts_v2,
    check_cross_artifact_consistency,
    check_single_artifact,
    score_plan_live,
)
from fitz_forge.planning.validation.grounding import StructuralIndexLookup


def test_score_plan_live_returns_not_applicable_when_no_artifacts():
    result = score_plan_live({"design": {"artifacts": []}})
    assert isinstance(result, LiveScore)
    assert result.applicable is False
    assert result.artifact_count == 0
    assert result.total == 0.0


def test_score_plan_live_returns_not_applicable_when_design_missing():
    result = score_plan_live({})
    assert result.applicable is False


def test_score_plan_live_clean_artifact_scores_high():
    # A parseable, non-fabricated, well-behaved artifact should score near top.
    plan = {
        "design": {
            "artifacts": [
                {
                    "filename": "example.py",
                    "content": (
                        "def hello(name: str) -> str:\n"
                        '    """Greet the user."""\n'
                        "    return f'hi {name}'\n"
                    ),
                }
            ]
        }
    }
    result = score_plan_live(plan)
    assert result.applicable is True
    assert result.artifact_count == 1
    # Total is capped at 70 (50 artifact + 20 consistency)
    assert 0.0 <= result.total <= 70.0
    # Clean code should score reasonably well
    assert result.artifact_quality > 30.0
    # No sibling, no inter-file issues -> consistency should be full
    assert result.consistency == 20.0


def test_score_plan_live_penalizes_unparseable_content():
    plan = {
        "design": {
            "artifacts": [
                {
                    "filename": "bad.py",
                    # Deliberately garbage that can't parse even after dedent/wrap.
                    "content": "def (((((\n",
                }
            ]
        }
    }
    clean = {
        "design": {
            "artifacts": [
                {
                    "filename": "good.py",
                    "content": "def ok() -> int:\n    return 1\n",
                }
            ]
        }
    }
    bad_score = score_plan_live(plan)
    good_score = score_plan_live(clean)
    assert good_score.artifact_quality > bad_score.artifact_quality


def test_score_plan_live_total_is_sum_of_parts():
    plan = {
        "design": {
            "artifacts": [
                {
                    "filename": "x.py",
                    "content": "def f() -> int:\n    return 1\n",
                }
            ]
        }
    }
    result = score_plan_live(plan)
    # Allow rounding wobble of 0.1
    assert abs(result.total - (result.artifact_quality + result.consistency)) < 0.2


def test_check_single_artifact_produces_artifact_check():
    lookup = StructuralIndexLookup("")
    artifact = {"filename": "foo.py", "content": "def f():\n    return 1\n"}
    result = check_single_artifact(artifact, lookup, task_requires_streaming=False)
    assert isinstance(result, ArtifactCheck)
    assert result.parseable is True


def test_check_all_artifacts_v2_batch_wrapper():
    artifacts = [
        {"filename": "a.py", "content": "def f() -> int:\n    return 1\n"},
        {"filename": "b.py", "content": "def g() -> str:\n    return 'x'\n"},
    ]
    results = check_all_artifacts_v2(artifacts, "", task_requires_streaming=False)
    assert len(results) == 2
    assert all(isinstance(r, ArtifactCheck) for r in results)
    assert all(r.parseable for r in results)


def test_check_cross_artifact_consistency_no_duplicates_passes():
    artifacts = [
        {"filename": "a.py", "content": "def f():\n    return 1\n"},
        {"filename": "b.py", "content": "def g():\n    return 2\n"},
    ]
    results = check_cross_artifact_consistency(artifacts)
    assert all(isinstance(r, ConsistencyResult) for r in results)
    # All checks should pass for clean, independent artifacts
    assert all(c.passed for c in results)


def test_benchmark_scorer_still_returns_pydantic_report():
    """The benchmark wrapper should still produce a DeterministicReport.

    Guards against breaking the existing eval loop when we moved helpers.
    """
    from benchmarks.eval_v2_deterministic import run_deterministic_checks

    plan = {
        "design": {
            "artifacts": [
                {
                    "filename": "example.py",
                    "content": "def f() -> int:\n    return 1\n",
                }
            ]
        },
        "decision_decomposition": {"decisions": []},
    }
    report = run_deterministic_checks(plan, structural_index="")
    # Must expose the scored fields the bench UI consumes.
    assert hasattr(report, "completeness_score")
    assert hasattr(report, "artifact_quality_score")
    assert hasattr(report, "consistency_score")
    assert hasattr(report, "deterministic_score")
    assert 0.0 <= report.deterministic_score <= 100.0
