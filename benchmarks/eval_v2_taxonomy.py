# benchmarks/eval_v2_taxonomy.py
"""
Tier 2: Taxonomy classification for Scorer V2.

Task-agnostic framework that loads taxonomy definitions from JSON.
Builds a Sonnet prompt for classification, parses the response,
and computes the taxonomy score.

Sonnet CLASSIFIES into taxonomy entries — it does NOT score.
The scoring formula is fixed and deterministic once classification is done.
"""

import json
import logging
from pathlib import Path

from .eval_v2_schemas import (
    ArtifactClassification,
    DeterministicReport,
    TaxonomyDefinition,
    TaxonomyEntry,
    TaxonomyReport,
    TaxonomyTable,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Taxonomy loading
# ---------------------------------------------------------------------------


def load_taxonomy(path: Path) -> TaxonomyDefinition:
    """Load a taxonomy definition from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))

    arch_table = TaxonomyTable(
        name=data["architecture_taxonomy"]["name"],
        entries=[TaxonomyEntry(**e) for e in data["architecture_taxonomy"]["entries"]],
    )

    file_tables = {}
    for key, table_data in data.get("file_taxonomies", {}).items():
        file_tables[key] = TaxonomyTable(
            name=table_data["name"],
            entries=[TaxonomyEntry(**e) for e in table_data["entries"]],
        )

    return TaxonomyDefinition(
        task_name=data["task_name"],
        task_description=data["task_description"],
        required_files=data.get("required_files", {}),
        architecture_taxonomy=arch_table,
        file_taxonomies=file_tables,
    )


# ---------------------------------------------------------------------------
# Taxonomy score lookup
# ---------------------------------------------------------------------------


def _lookup_score(table: TaxonomyTable, taxonomy_id: str) -> int:
    """Find the score for a taxonomy entry by ID."""
    for entry in table.entries:
        if entry.id == taxonomy_id:
            return entry.score
    logger.warning(f"Taxonomy ID '{taxonomy_id}' not found in {table.name}")
    return 0


def _match_file_to_taxonomy(
    filename: str,
    file_taxonomies: dict[str, TaxonomyTable],
) -> TaxonomyTable | None:
    """Match an artifact filename to a taxonomy table."""
    # Try exact match first, then suffix match
    for key, table in file_taxonomies.items():
        if filename == key or filename.endswith(key) or key.endswith(filename):
            return table
    # Try basename match
    basename = filename.rsplit("/", 1)[-1]
    for key, table in file_taxonomies.items():
        key_base = key.rsplit("/", 1)[-1]
        if basename == key_base:
            return table
    return None


# ---------------------------------------------------------------------------
# Sonnet classification prompt
# ---------------------------------------------------------------------------


def _format_taxonomy_table(table: TaxonomyTable) -> str:
    """Format a taxonomy table as markdown for the prompt."""
    lines = [f"### {table.name}\n"]
    lines.append("| ID | Pattern | Quality | Description |")
    lines.append("|----|---------|---------|-------------|")
    for e in table.entries:
        lines.append(f"| {e.id} | {e.pattern} | {e.quality} | {e.description} |")
    return "\n".join(lines)


def build_taxonomy_prompt(
    plan_json: str,
    deterministic_report: DeterministicReport,
    taxonomy: TaxonomyDefinition,
    structural_index: str,
) -> str:
    """Build the Sonnet prompt for taxonomy classification.

    Sonnet receives the plan, deterministic results, taxonomy tables,
    and structural index. It classifies each artifact and the overall
    architecture into taxonomy entries.
    """
    parts = []

    # System instructions
    parts.append(
        "You are an expert software architect classifying implementation plans "
        "against a predefined taxonomy. Your job is to CLASSIFY, not to score.\n\n"
        "Rules:\n"
        "1. Pick the taxonomy entry that best matches each artifact\n"
        "2. Pick the overall architecture taxonomy entry\n"
        "3. Do NOT override deterministic findings — if Tier 1 found 3 fabricated "
        "methods, you cannot say 'actually it's fine'\n"
        "4. Note any issues the deterministic checks missed (semantic errors, "
        "wrong algorithms)\n"
        "5. Respond with ONLY the JSON object described below"
    )

    # Task context
    parts.append(f"## Task\n{taxonomy.task_description}")

    # Structural index (codebase context)
    if structural_index:
        # Truncate if very large — Sonnet doesn't need the full 120K index
        idx = structural_index[:50000] if len(structural_index) > 50000 else structural_index
        parts.append(f"## Target Codebase Structure\n{idx}")

    # Taxonomy tables
    parts.append("## Architecture Taxonomy")
    parts.append(_format_taxonomy_table(taxonomy.architecture_taxonomy))

    parts.append("## Per-File Taxonomies")
    for key, table in taxonomy.file_taxonomies.items():
        parts.append(_format_taxonomy_table(table))

    # Deterministic results summary
    parts.append("## Deterministic Check Results (Tier 1)\n")
    parts.append(f"- Deterministic score: {deterministic_report.deterministic_score}/100")
    parts.append(f"- Completeness: {deterministic_report.completeness_score}/30")
    parts.append(f"- Artifact quality: {deterministic_report.artifact_quality_score}/50")
    parts.append(f"- Consistency: {deterministic_report.consistency_score}/20")

    parts.append("\n### Per-Artifact Results")
    for ac in deterministic_report.artifact_checks:
        parts.append(
            f"- **{ac.filename}**: {ac.score:.0f}/100 "
            f"({ac.checks_passed}/{ac.checks_total} checks passed, "
            f"{ac.violation_count} violations, "
            f"parseable={ac.parseable}, yield={ac.has_yield}, "
            f"NotImplementedError={ac.has_not_implemented})"
        )

    parts.append("\n### Consistency")
    for cc in deterministic_report.consistency_checks:
        status = "PASS" if cc.passed else "FAIL"
        parts.append(f"- [{status}] {cc.check}: {cc.detail}")

    parts.append("\n### Missing Required Files")
    for f in deterministic_report.completeness.missing_required:
        parts.append(f"- {f}")

    # The plan itself
    parts.append(f"## Plan to Classify\n```json\n{plan_json}\n```")

    # Build the artifact list for structured output
    artifact_filenames = [ac.filename for ac in deterministic_report.artifact_checks]

    # Response format
    parts.append(
        "## Classification Instructions\n\n"
        "Classify the overall architecture and each artifact into the taxonomy "
        "entries above. For each artifact, pick the ID that best matches.\n\n"
        "For artifacts that don't match any file taxonomy (e.g., schemas, SDK files), "
        "skip them in artifact_classifications.\n\n"
        "Respond with ONLY a JSON object in this exact format:\n"
        "```json\n"
        "{\n"
        '  "architecture": {"id": "A1-A5", "confidence": "high|medium|low"},\n'
        '  "artifacts": [\n'
    )

    for fn in artifact_filenames:
        parts.append(
            f'    {{"filename": "{fn}", "id": "XX", "confidence": "high|medium|low", "notes": "..."}}'
        )

    parts.append(
        "  ],\n"
        '  "qualitative_notes": "Issues not captured by deterministic checks"\n'
        "}\n"
        "```"
    )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Parse Sonnet classification response
# ---------------------------------------------------------------------------


def parse_taxonomy_response(
    raw_text: str,
    taxonomy: TaxonomyDefinition,
) -> TaxonomyReport:
    """Parse Sonnet's JSON classification into a TaxonomyReport with scores."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)

    data = json.loads(text)

    # Architecture classification
    arch_data = data.get("architecture", {})
    arch_id = arch_data.get("id", "A5")
    arch_confidence = arch_data.get("confidence", "medium")
    arch_score = _lookup_score(taxonomy.architecture_taxonomy, arch_id)

    # Per-artifact classifications
    artifact_classifications = []
    per_file_scores = []

    for art_data in data.get("artifacts", []):
        filename = art_data.get("filename", "")
        tax_id = art_data.get("id", "")
        confidence = art_data.get("confidence", "medium")
        notes = art_data.get("notes", "")

        # Find matching taxonomy table
        table = _match_file_to_taxonomy(filename, taxonomy.file_taxonomies)
        if table:
            score = _lookup_score(table, tax_id)
            per_file_scores.append(score)
        else:
            score = None  # No taxonomy for this file

        artifact_classifications.append(ArtifactClassification(
            filename=filename,
            taxonomy_id=tax_id,
            confidence=confidence,
            notes=notes,
        ))

    qualitative_notes = data.get("qualitative_notes", "")

    # Score calculation
    per_file_mean = (
        sum(per_file_scores) / len(per_file_scores) if per_file_scores else 0.0
    )
    taxonomy_score = round(arch_score * 0.4 + per_file_mean * 0.6, 1)

    return TaxonomyReport(
        architecture_classification=arch_id,
        architecture_confidence=arch_confidence,
        artifact_classifications=artifact_classifications,
        qualitative_notes=qualitative_notes,
        architecture_score=float(arch_score),
        per_file_score=round(per_file_mean, 1),
        taxonomy_score=taxonomy_score,
    )
