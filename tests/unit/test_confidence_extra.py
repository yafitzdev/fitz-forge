# tests/unit/test_confidence_extra.py
"""
Additional confidence tests covering gaps in test_confidence.py.

Covers: section-specific criteria in prompts, edge cases, overall scoring flow.
"""

import pytest

from fitz_graveyard.config.schema import ConfidenceConfig
from fitz_graveyard.planning.confidence import ConfidenceScorer, SectionFlagger


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


class _CapturingMockLLM:
    """Mock that captures the prompt for assertion."""

    def __init__(self, response: str = "7"):
        self.response = response
        self.last_prompt = ""

    async def generate(self, messages: list[dict], **kwargs) -> str:
        self.last_prompt = messages[-1]["content"]
        return self.response


# ---------------------------------------------------------------------------
# ConfidenceScorer: section-specific criteria
# ---------------------------------------------------------------------------


class TestScorerSectionCriteria:
    """Verify section-specific criteria are injected into LLM prompt."""

    @pytest.mark.asyncio
    async def test_context_criteria_in_prompt(self):
        """'context' section includes its specific criteria."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        await scorer.score_section("context", "Some content")

        assert "testable requirements" in llm.last_prompt

    @pytest.mark.asyncio
    async def test_architecture_criteria_in_prompt(self):
        """'architecture' section includes its specific criteria."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        await scorer.score_section("architecture", "Some content")

        assert "already implemented" in llm.last_prompt

    @pytest.mark.asyncio
    async def test_design_criteria_in_prompt(self):
        """'design' section includes its specific criteria."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        await scorer.score_section("design", "Some content")

        assert "real function signatures" in llm.last_prompt

    @pytest.mark.asyncio
    async def test_roadmap_criteria_in_prompt(self):
        """'roadmap' section includes its specific criteria."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        await scorer.score_section("roadmap", "Some content")

        assert "concrete deliverables" in llm.last_prompt

    @pytest.mark.asyncio
    async def test_risk_criteria_in_prompt(self):
        """'risk' section includes its specific criteria."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        await scorer.score_section("risk", "Some content")

        assert "specific technical causes" in llm.last_prompt

    @pytest.mark.asyncio
    async def test_unknown_section_no_criteria(self):
        """Unknown section name still works (no criteria block)."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        score = await scorer.score_section("unknown_section", "Some content")

        assert 0.0 <= score <= 1.0
        # Should not contain section-specific criteria
        assert "Section-specific criteria" not in llm.last_prompt

    @pytest.mark.asyncio
    async def test_codebase_context_adds_grounding(self):
        """Codebase context injects grounding check criterion."""
        llm = _CapturingMockLLM()
        scorer = ConfidenceScorer(ollama_client=llm)

        await scorer.score_section(
            "architecture", "Content",
            codebase_context="## Files\n- src/api.py",
        )

        assert "GROUNDING CHECK" in llm.last_prompt
        assert "src/api.py" in llm.last_prompt


# ---------------------------------------------------------------------------
# ConfidenceScorer: edge cases
# ---------------------------------------------------------------------------


class TestScorerEdgeCases:
    """Edge cases for heuristic scoring."""

    @pytest.mark.asyncio
    async def test_empty_content(self):
        """Empty content scores very low."""
        scorer = ConfidenceScorer(ollama_client=None)
        score = await scorer.score_section("test", "")

        assert score < 0.5

    @pytest.mark.asyncio
    async def test_content_with_more_vague_than_specific(self):
        """Content with more vague than specific keywords scores 0.6."""
        scorer = ConfidenceScorer(ollama_client=None)
        content = (
            "This module maybe works. "
            "It could possibly fail, should probably be replaced, "
            "but the example is unclear and TBD with a placeholder."
        )
        score = scorer._specificity_score(content)
        # Specific: module (1)
        # Vague: maybe, could, possibly, should, probably, example, unclear, tbd, placeholder (9)
        # vague_count > specificity_count → 0.6
        assert score == 0.6

    @pytest.mark.asyncio
    async def test_heuristic_score_rounds_to_2_decimals(self):
        """Heuristic score rounds to 2 decimal places."""
        scorer = ConfidenceScorer(ollama_client=None)
        score = await scorer.score_section("test", "Short content.")

        # Check it's rounded
        assert score == round(score, 2)

    @pytest.mark.asyncio
    async def test_hybrid_score_rounds_to_2_decimals(self):
        """Hybrid score rounds to 2 decimal places."""
        llm = _CapturingMockLLM(response="6")
        scorer = ConfidenceScorer(ollama_client=llm)

        score = await scorer.score_section("test", "Short content.")

        assert score == round(score, 2)


# ---------------------------------------------------------------------------
# SectionFlagger: edge cases
# ---------------------------------------------------------------------------


class TestFlaggerEdgeCases:
    """Edge cases for SectionFlagger."""

    def test_exact_threshold_not_flagged(self):
        """Score exactly at threshold is NOT flagged (< not <=)."""
        flagger = SectionFlagger(default_threshold=0.7)
        flagged, _ = flagger.flag_section("context", 0.7)
        assert not flagged

    def test_just_below_threshold_flagged(self):
        """Score just below threshold IS flagged."""
        flagger = SectionFlagger(default_threshold=0.7)
        flagged, reason = flagger.flag_section("context", 0.69)
        assert flagged
        assert "0.69" in reason

    def test_zero_score_flagged(self):
        """Score of 0.0 is always flagged."""
        flagger = SectionFlagger(default_threshold=0.7)
        flagged, _ = flagger.flag_section("context", 0.0)
        assert flagged

    def test_perfect_score_not_flagged(self):
        """Score of 1.0 is never flagged."""
        flagger = SectionFlagger(default_threshold=0.7, security_threshold=0.9)
        flagged, _ = flagger.flag_section("security", 1.0)
        assert not flagged

    def test_overall_score_single_section(self):
        """Overall score with single section equals that section's score."""
        flagger = SectionFlagger()
        overall = flagger.compute_overall_score({"context": 0.85})
        assert overall == 0.85

    def test_security_keyword_in_compound_name(self):
        """Section name containing security keyword uses security threshold."""
        flagger = SectionFlagger(default_threshold=0.5, security_threshold=0.9)
        flagged, reason = flagger.flag_section("api_authentication_layer", 0.85)
        assert flagged
        assert "Security-sensitive" in reason

    def test_from_config_custom_values(self):
        """from_config passes through custom threshold values."""
        config = ConfidenceConfig(default_threshold=0.6, security_threshold=0.8)
        flagger = SectionFlagger.from_config(config)

        # 0.75 is above 0.6 (default) but below 0.8 (security)
        flagged_normal, _ = flagger.flag_section("context", 0.75)
        flagged_security, _ = flagger.flag_section("risk", 0.75)

        assert not flagged_normal
        assert flagged_security
