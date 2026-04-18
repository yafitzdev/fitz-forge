# tests/unit/test_artifact_generator_semantic_wiring.py
"""Integration tests for the semantic-review phase inside generate_artifact_set.

These exercise the wiring between generate_artifact_set's repair loop and
the LLM-backed semantic gate. Both generate_artifact and semantic_review
are monkeypatched so tests do not spin a real model — we're asserting
control flow (when the gate fires, when regen runs, when iters stop).
"""

from __future__ import annotations

import pytest

from fitz_forge.planning.artifact import generator as gen
from fitz_forge.planning.artifact.generator import (
    ArtifactResult,
    ArtifactSetResult,
    generate_artifact_set,
)
from fitz_forge.planning.artifact.semantic_review import Discrepancy, ReviewResult


class _StubLookup:
    """Minimal lookup that reports no augmentation and no class knowledge."""

    classes: dict = {}
    _all_class_names: frozenset = frozenset()

    def augment_from_source_dir(self, _src: str) -> None:  # pragma: no cover - shim
        return None

    def class_exists(self, _name: str) -> bool:
        return False

    def class_has_field(self, _a: str, _b: str) -> bool:
        return False

    def class_has_method(self, _a: str, _b: str) -> bool:
        return False

    def function_exists(self, _name: str) -> bool:
        return False

    def find_function(self, _name: str) -> list:
        return []

    def find_classes(self, _name: str) -> list:
        return []


def _make_result(filename: str, content: str = "pass", *, success: bool = True) -> ArtifactResult:
    return ArtifactResult(
        filename=filename,
        content=content,
        purpose=f"purpose for {filename}",
        success=success,
        strategy="new_code",
    )


@pytest.fixture
def patched_deps(monkeypatch):
    """Patch out the heavy/external collaborators in generator."""

    # check_closure → always reports no violations (closure closes)
    monkeypatch.setattr(gen, "check_closure", lambda *a, **k: [])

    # Skip the real lookup construction in grounding.
    from fitz_forge.planning.validation import grounding

    monkeypatch.setattr(
        grounding, "StructuralIndexLookup", lambda _text: _StubLookup()
    )

    calls: dict[str, list] = {"generate_artifact": [], "semantic_review": []}

    def _stub_generate_artifact_factory(per_file_output):
        async def _stub(**kwargs):
            calls["generate_artifact"].append(kwargs)
            fn = kwargs["filename"]
            return per_file_output(fn, kwargs)

        return _stub

    def _stub_semantic_review_factory(reviews):
        """reviews: list of ReviewResult to return in order."""
        it = iter(reviews)

        async def _stub(*, reasoning, decisions, artifacts, client, **kwargs):
            calls["semantic_review"].append(
                {
                    "reasoning": reasoning,
                    "decisions": decisions,
                    "artifacts": artifacts,
                }
            )
            try:
                return next(it)
            except StopIteration:  # pragma: no cover - guardrail
                return ReviewResult(matches_intent=True)

        return _stub

    return {
        "monkeypatch": monkeypatch,
        "calls": calls,
        "make_generate_artifact": _stub_generate_artifact_factory,
        "make_semantic_review": _stub_semantic_review_factory,
    }


# ---------------------------------------------------------------------------
# max_semantic_iters = 0 disables the gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_review_disabled_by_zero_iters(patched_deps):
    calls = patched_deps["calls"]
    mp = patched_deps["monkeypatch"]

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](
            lambda fn, _k: _make_result(fn, content=f"# {fn}")
        ),
    )
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"]([]),
    )

    result = await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A"), ("b.py", "B")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="intent",
        max_semantic_iters=0,
    )

    assert isinstance(result, ArtifactSetResult)
    assert result.semantic_review_iterations == 0
    assert result.semantic_review_matched is True
    assert calls["semantic_review"] == []
    # one call per initial spec, no regen
    assert len(calls["generate_artifact"]) == 2


# ---------------------------------------------------------------------------
# Gate matches on first pass → no regen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matches_on_first_pass_no_regeneration(patched_deps):
    calls = patched_deps["calls"]
    mp = patched_deps["monkeypatch"]

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](
            lambda fn, _k: _make_result(fn, content=f"# {fn}")
        ),
    )
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"](
            [ReviewResult(matches_intent=True, discrepancies=[])]
        ),
    )

    result = await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="intent",
        resolved_decisions=[{"decision_id": "d1", "decision": "x"}],
    )

    assert result.semantic_review_iterations == 1
    assert result.semantic_review_matched is True
    assert len(calls["semantic_review"]) == 1
    # 1 initial generate_artifact, no regen
    assert len(calls["generate_artifact"]) == 1


# ---------------------------------------------------------------------------
# Discrepancy → regenerate → second review passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discrepancy_triggers_regeneration_then_matches(patched_deps):
    calls = patched_deps["calls"]
    mp = patched_deps["monkeypatch"]

    # The regen result overwrites the first artifact's content so we can
    # verify it was swapped into the result list.
    def _per_file(fn: str, kwargs: dict) -> ArtifactResult:
        purpose = kwargs.get("purpose", "")
        tag = "regen" if "Semantic review feedback" in purpose else "first"
        return _make_result(fn, content=f"# {fn} {tag}")

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](_per_file),
    )

    reviews = [
        ReviewResult(
            matches_intent=False,
            discrepancies=[
                Discrepancy(
                    file="a.py",
                    line=1,
                    intent="do X",
                    actual="does Y",
                    fix="do X",
                )
            ],
        ),
        ReviewResult(matches_intent=True, discrepancies=[]),
    ]
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"](reviews),
    )

    result = await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="intent",
        resolved_decisions=[],
    )

    assert result.semantic_review_iterations == 2
    assert result.semantic_review_matched is True
    # one first generate + one regen after discrepancy
    regen_calls = [
        c for c in calls["generate_artifact"] if "Semantic review feedback" in c.get("purpose", "")
    ]
    assert len(regen_calls) == 1
    a_result = next(r for r in result.results if r.filename == "a.py")
    assert "regen" in a_result.content


# ---------------------------------------------------------------------------
# Budget exhausted — review_iterations caps, semantic_review_matched=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_exhausted_persistent_discrepancy(patched_deps):
    mp = patched_deps["monkeypatch"]

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](lambda fn, _k: _make_result(fn)),
    )
    # Both iterations report discrepancies — gate never matches.
    persistent = ReviewResult(
        matches_intent=False,
        discrepancies=[
            Discrepancy(
                file="a.py",
                line=1,
                intent="do X",
                actual="does Y",
                fix="do X",
            )
        ],
    )
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"]([persistent, persistent]),
    )

    result = await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="intent",
        max_semantic_iters=2,
    )

    assert result.semantic_review_iterations == 2
    assert result.semantic_review_matched is False
    assert len(result.semantic_review_discrepancies) == 1


# ---------------------------------------------------------------------------
# Gate routes discrepancies to the correct file only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_offending_files_regenerated(patched_deps):
    calls = patched_deps["calls"]
    mp = patched_deps["monkeypatch"]

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](lambda fn, _k: _make_result(fn)),
    )
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"](
            [
                ReviewResult(
                    matches_intent=False,
                    discrepancies=[
                        Discrepancy(
                            file="a.py",
                            line=1,
                            intent="i",
                            actual="a",
                            fix="f",
                        )
                    ],
                ),
                ReviewResult(matches_intent=True),
            ]
        ),
    )

    await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A"), ("b.py", "B"), ("c.py", "C")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="intent",
    )

    regen_calls = [
        c for c in calls["generate_artifact"] if "Semantic review feedback" in c.get("purpose", "")
    ]
    # Only a.py should have been regenerated.
    assert len(regen_calls) == 1
    assert regen_calls[0]["filename"] == "a.py"


# ---------------------------------------------------------------------------
# Gate receives reasoning + decisions + artifact contents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_is_handed_reasoning_decisions_and_artifacts(patched_deps):
    calls = patched_deps["calls"]
    mp = patched_deps["monkeypatch"]

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](
            lambda fn, _k: _make_result(fn, content=f"BODY-{fn}")
        ),
    )
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"]([ReviewResult(matches_intent=True)]),
    )

    await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="REASONING-X",
        resolved_decisions=[{"decision_id": "D1", "decision": "DEC-X"}],
    )

    assert len(calls["semantic_review"]) == 1
    sent = calls["semantic_review"][0]
    assert sent["reasoning"] == "REASONING-X"
    assert sent["decisions"] == [{"decision_id": "D1", "decision": "DEC-X"}]
    files = [a["filename"] for a in sent["artifacts"]]
    contents = [a["content"] for a in sent["artifacts"]]
    assert files == ["a.py"]
    assert contents == ["BODY-a.py"]


# ---------------------------------------------------------------------------
# Empty artifact set short-circuits the gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_successful_artifacts_skips_gate(patched_deps):
    calls = patched_deps["calls"]
    mp = patched_deps["monkeypatch"]

    mp.setattr(
        gen,
        "generate_artifact",
        patched_deps["make_generate_artifact"](
            lambda fn, _k: _make_result(fn, success=False)
        ),
    )
    mp.setattr(
        gen,
        "semantic_review",
        patched_deps["make_semantic_review"]([]),
    )

    result = await generate_artifact_set(
        client=object(),
        specs=[("a.py", "A")],
        source_dir="",
        structural_index="",
        decisions_for=lambda _fn: "",
        reasoning="intent",
    )

    assert result.semantic_review_iterations == 0
    assert calls["semantic_review"] == []
