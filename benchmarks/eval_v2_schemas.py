# benchmarks/eval_v2_schemas.py
"""
Pydantic schemas for Scorer V2.

Three-layer output:
  - Tier 1: Deterministic checks (completeness, per-artifact AST, cross-artifact consistency)
  - Tier 2: Taxonomy classification (Sonnet classifies against rubric)
  - Combined: weighted final score
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Tier 1: Deterministic checks
# ---------------------------------------------------------------------------


class ArtifactCheck(BaseModel):
    """AST validation result for a single artifact."""

    model_config = ConfigDict(extra="ignore")

    filename: str
    content_lines: int = Field(default=0, description="Line count of artifact content")
    parseable: bool = Field(description="ast.parse() succeeded")
    fabricated_self_methods: int = Field(
        default=0, description="Count of self.method() calls not in index"
    )
    fabricated_chained_methods: int = Field(
        default=0, description="Count of self._xxx.method() calls not in index"
    )
    fabricated_field_access: int = Field(
        default=0, description="Count of obj.field accesses on typed params not in index"
    )
    fabricated_classes: int = Field(
        default=0, description="Count of ClassName() constructors not in index"
    )
    has_yield: bool | None = Field(
        default=None,
        description="True if yield present (None if not required for this artifact)",
    )
    has_correct_return_type: bool | None = Field(
        default=None,
        description="True if function annotation matches purpose (None if not checked)",
    )
    has_not_implemented: bool = Field(
        default=False, description="True if NotImplementedError found in content"
    )
    has_sys_stdout: bool = Field(
        default=False, description="True if sys.stdout found in content"
    )
    violation_count: int = Field(
        default=0, description="Total violations from grounding check_artifact"
    )
    checks_passed: int = Field(default=0, description="Number of checks that passed")
    checks_total: int = Field(default=0, description="Total number of checks run")
    score: float = Field(
        default=0.0, description="Per-artifact score: checks_passed / checks_total * 100"
    )


class ConsistencyResult(BaseModel):
    """Cross-artifact consistency check result."""

    model_config = ConfigDict(extra="ignore")

    check: str = Field(description="What was checked")
    passed: bool
    detail: str = Field(default="", description="Explanation if failed")


class CompletenessResult(BaseModel):
    """File completeness check result."""

    model_config = ConfigDict(extra="ignore")

    required_files: list[str] = Field(description="Files referenced in 3+ decisions")
    recommended_files: list[str] = Field(
        description="Files referenced in 2 decisions"
    )
    optional_files: list[str] = Field(
        description="Files referenced in 1 decision"
    )
    present_required: list[str] = Field(description="Required files found in plan")
    present_recommended: list[str] = Field(description="Recommended files found in plan")
    present_optional: list[str] = Field(description="Optional files found in plan")
    missing_required: list[str] = Field(description="Required files NOT in plan")
    required_ratio: float = Field(
        description="present_required / required_files (0-1)"
    )
    score: float = Field(description="Completeness score (0-1) with bonuses")


class DeterministicReport(BaseModel):
    """Full Tier 1 deterministic check output."""

    model_config = ConfigDict(extra="ignore")

    completeness: CompletenessResult
    artifact_checks: list[ArtifactCheck]
    consistency_checks: list[ConsistencyResult]

    completeness_score: float = Field(description="0-30 points")
    artifact_quality_score: float = Field(description="0-50 points")
    consistency_score: float = Field(description="0-20 points")
    deterministic_score: float = Field(description="Sum of above, 0-100")


# ---------------------------------------------------------------------------
# Tier 2: Taxonomy classification
# ---------------------------------------------------------------------------


class TaxonomyEntry(BaseModel):
    """A single entry in a taxonomy table."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Pattern ID (e.g. A1, E3, R2)")
    pattern: str = Field(description="Short pattern name")
    quality: str = Field(description="BEST, GOOD, PARTIAL, POOR, BAD, FAIL, ABSENT")
    description: str = Field(default="", description="What this pattern looks like")
    score: int = Field(description="Numeric score for this pattern (0-100)")


class TaxonomyTable(BaseModel):
    """A taxonomy table for a specific file or overall architecture."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Table name (e.g. 'architecture', 'engine.py')")
    entries: list[TaxonomyEntry]


class TaxonomyDefinition(BaseModel):
    """Complete taxonomy definition for a task."""

    model_config = ConfigDict(extra="ignore")

    task_name: str
    task_description: str
    required_files: dict[str, str] = Field(
        default_factory=dict,
        description="File pattern -> tier ('required', 'recommended', 'optional')",
    )
    architecture_taxonomy: TaxonomyTable
    file_taxonomies: dict[str, TaxonomyTable] = Field(
        description="Keyed by file path pattern (e.g. 'engine.py')"
    )


class ArtifactClassification(BaseModel):
    """Sonnet's classification of a single artifact."""

    model_config = ConfigDict(extra="ignore")

    filename: str
    taxonomy_id: str = Field(description="Which taxonomy entry (e.g. E3, R2)")
    confidence: str = Field(
        default="high", description="high, medium, low"
    )
    notes: str = Field(default="", description="Issues deterministic checks missed")


class TaxonomyReport(BaseModel):
    """Full Tier 2 taxonomy classification output."""

    model_config = ConfigDict(extra="ignore")

    architecture_classification: str = Field(
        description="Architecture taxonomy ID (e.g. A1)"
    )
    architecture_confidence: str = Field(default="high")
    artifact_classifications: list[ArtifactClassification]
    qualitative_notes: str = Field(
        default="", description="Issues not captured by deterministic checks"
    )

    architecture_score: float = Field(description="0-100 from taxonomy lookup")
    per_file_score: float = Field(description="Mean of per-file taxonomy scores, 0-100")
    taxonomy_score: float = Field(
        description="arch_score * 0.4 + per_file_score * 0.6"
    )


# ---------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------


class PlanScoreV2(BaseModel):
    """Complete V2 evaluation result for a single plan."""

    model_config = ConfigDict(extra="ignore")

    plan_file: str
    query: str

    deterministic: DeterministicReport
    taxonomy: TaxonomyReport | None = Field(
        default=None, description="None if taxonomy classification not yet run"
    )

    deterministic_score: float = Field(description="Tier 1 score, 0-100")
    taxonomy_score: float | None = Field(
        default=None, description="Tier 2 score, 0-100 (None if not classified)"
    )
    final_score: float = Field(
        description="deterministic * 0.6 + taxonomy * 0.4 (or just deterministic if no taxonomy)"
    )

    scored_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class BatchScoreV2(BaseModel):
    """Aggregate V2 results from scoring multiple plans."""

    model_config = ConfigDict(extra="ignore")

    query: str
    scored_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    plans_scored: int
    deterministic_average: float
    deterministic_min: float
    deterministic_max: float
    taxonomy_average: float | None = None
    final_average: float
    final_min: float
    final_max: float
    scores: list[PlanScoreV2]
