# fitz_forge/planning/artifact/strategy.py
"""Pluggable artifact generation strategies.

Each strategy knows how to build a prompt and handle retries.
The generator picks the right strategy based on context.
"""

import ast
import json
import logging
from typing import Any, Protocol

from fitz_forge.llm.generate import generate
from fitz_forge.planning.pipeline.stages.base import SYSTEM_PROMPT, extract_json

from .context import ArtifactContext
from .validate import ArtifactError

logger = logging.getLogger(__name__)

_JSON_SCHEMA = json.dumps(
    {
        "filename": "path/to/file.py",
        "content": "ONLY the new methods/classes to add — not the entire file",
        "purpose": "why this artifact exists",
    },
    indent=2,
)


class ArtifactStrategy(Protocol):
    """Interface for artifact generation strategies."""

    @property
    def name(self) -> str: ...

    async def generate(self, client: Any, ctx: ArtifactContext) -> str:
        """Generate artifact content. Returns raw code string."""
        ...

    def build_retry_prompt(
        self,
        ctx: ArtifactContext,
        previous_content: str,
        errors: list[ArtifactError],
    ) -> list[dict]:
        """Build a retry prompt with error feedback."""
        ...


def _make_messages(content: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


class SurgicalRewriteStrategy:
    """Rewrite an existing method with minimal prompt.

    Gives the model ONLY the reference method + one instruction.
    Fresh context prevents shortcutting. Low fabrication rate.
    """

    name = "surgical"

    async def generate(self, client: Any, ctx: ArtifactContext) -> str:
        # Find the output line (last return statement) in the reference
        change_hint = _extract_change_hint(ctx.reference_method)

        prompt = (
            f"Rewrite this existing method to: {ctx.purpose}\n\n"
            f"## EXISTING METHOD (copy this structure exactly)\n"
            f"```python\n{ctx.reference_method}\n```\n\n"
            f"## INSTRUCTIONS\n"
            f"1. Copy the method above, renaming it appropriately\n"
            f"2. Keep ALL internal pipeline steps (every self._xxx call) "
            f"in the same order — these do retrieval, validation, "
            f"enrichment, etc. that must not be skipped\n"
            f"3. Do NOT skip steps or call lower-level primitives directly\n"
            f"{change_hint}\n"
            f"Return ONLY valid JSON matching this schema:\n{_JSON_SCHEMA}\n"
        )

        safe = ctx.filename.replace("/", "_").replace("\\", "_")
        raw = await generate(
            client,
            messages=_make_messages(prompt),
            max_tokens=8192,
            label=f"artifact_surgical_{safe}",
        )
        data = extract_json(raw)
        return data.get("content", "")

    def build_retry_prompt(
        self,
        ctx: ArtifactContext,
        previous_content: str,
        errors: list[ArtifactError],
    ) -> list[dict]:
        error_text = "\n".join(
            f"- {e.check.upper()}: {e.message}\n  FIX: {e.suggestion}"
            for e in errors
            if e.check != "not_implemented"
        )

        # Re-include the full surgical instructions + change hint so
        # the model has enough context to produce a complete response.
        change_hint = _extract_change_hint(ctx.reference_method)

        prompt = (
            f"Rewrite this existing method to: {ctx.purpose}\n\n"
            f"## EXISTING METHOD (copy this structure exactly)\n"
            f"```python\n{ctx.reference_method}\n```\n\n"
            f"## INSTRUCTIONS\n"
            f"1. Copy the method above, renaming it appropriately\n"
            f"2. Keep ALL internal pipeline steps (every self._xxx call) "
            f"in the same order\n"
            f"3. Do NOT skip steps or call lower-level primitives directly\n"
            f"{change_hint}\n"
            f"## ERRORS IN YOUR PREVIOUS ATTEMPT (fix these)\n"
            f"{error_text}\n\n"
            f"Your previous output:\n"
            f"```python\n{previous_content}\n```\n\n"
            f"Return ONLY valid JSON matching this schema:\n{_JSON_SCHEMA}\n"
        )
        return _make_messages(prompt)


class NewCodeStrategy:
    """Full prompt for genuinely new code (no reference method).

    Includes source, decisions, interfaces, reasoning. Higher
    fabrication rate than surgical — more input = more confusion.
    """

    name = "new_code"

    async def generate(self, client: Any, ctx: ArtifactContext) -> str:
        prompt = self._build_prompt(ctx)
        safe = ctx.filename.replace("/", "_").replace("\\", "_")
        raw = await generate(
            client,
            messages=_make_messages(prompt),
            max_tokens=4096,
            label=f"artifact_{safe}",
        )
        data = extract_json(raw)
        return data.get("content", "")

    def _build_prompt(self, ctx: ArtifactContext) -> str:
        rules = (
            "Rules:\n"
            "- Write ONLY the new or modified code (not the entire file)\n"
            "- Use exact attribute names from the source code above\n"
            "- When calling self._xxx.method(), use ONLY methods listed "
            "in AVAILABLE METHODS above\n"
            "- When calling imported objects (e.g. service.xxx()), use ONLY "
            "methods listed in IMPORTED TYPE APIs above. If a method is not "
            "listed, it does NOT exist — do NOT assume it will be added later\n"
            "- If the method you need does NOT exist on a dependency, "
            "compose the behavior from its existing methods instead of "
            "inventing new ones\n"
            "- When adding a parallel method, match the original "
            "method's parameters exactly\n"
            "- Do NOT fabricate method names — if unsure, omit the call\n"
        )

        # Grounding block (high priority — goes first)
        grounding_parts = []
        if ctx.available_methods:
            grounding_parts.append(
                f"\n## AVAILABLE METHODS ON INSTANCE ATTRIBUTES\n"
                f"When calling methods on self._xxx, use ONLY these:\n"
                f"{ctx.available_methods}"
            )
        if ctx.schema_fields:
            grounding_parts.append(
                f"\n## DATA MODEL FIELDS (use these exact field names)\n{ctx.schema_fields}"
            )
        if ctx.param_type_fields:
            grounding_parts.append(f"\n## PARAMETER TYPE FIELDS\n{ctx.param_type_fields}")
        if ctx.prior_sigs:
            grounding_parts.append(ctx.prior_sigs)
        grounding = "\n".join(grounding_parts)

        # Source section
        if ctx.compressed_source:
            source_section = (
                f"\n\n## CURRENT SOURCE CODE of {ctx.filename}\n"
                f"Use ONLY the attributes, methods, and field names you "
                f"see below. Do NOT invent methods that aren't here.\n\n"
                f"```python\n{ctx.compressed_source}\n```"
            )
        else:
            source_section = (
                f"\n\n(Source code for {ctx.filename} not available. "
                f"Use method names from the decisions above.)"
            )

        # Budget-aware reasoning truncation
        _TOKEN_BUDGET_CHARS = 32000 * 4
        fixed = (
            f"Write a code artifact for: {ctx.filename}\n"
            f"Purpose: {ctx.purpose}\n\n"
            f"{rules}\n{grounding}\n\n"
            f"## RELEVANT DECISIONS\n{ctx.decisions}\n\n"
            f"{source_section}\n\n"
            f"Return ONLY valid JSON matching this schema:\n{_JSON_SCHEMA}\n"
        )
        reasoning_budget = max(500, _TOKEN_BUDGET_CHARS - len(fixed) - 200)

        from fitz_forge.planning.pipeline.stages.synthesis import _truncate_at_line

        reasoning_final = _truncate_at_line(ctx.reasoning, reasoning_budget)

        return (
            f"Write a code artifact for: {ctx.filename}\n"
            f"Purpose: {ctx.purpose}\n\n"
            f"{rules}\n{grounding}\n\n"
            f"## RELEVANT DECISIONS\n{ctx.decisions}\n\n"
            f"{source_section}\n\n"
            f"## PLAN CONTEXT (background — lower priority than above)\n"
            f"{reasoning_final}\n\n"
            f"Return ONLY valid JSON matching this schema:\n{_JSON_SCHEMA}\n"
        )

    def build_retry_prompt(
        self,
        ctx: ArtifactContext,
        previous_content: str,
        errors: list[ArtifactError],
    ) -> list[dict]:
        error_text = "\n".join(
            f"- {e.check.upper()}: {e.message}\n  FIX: {e.suggestion}"
            for e in errors
            if e.check != "not_implemented"
        )

        # Re-include the full prompt context so the model doesn't lose
        # track of what it's generating.
        full_prompt = self._build_prompt(ctx)
        prompt = (
            f"{full_prompt}\n\n"
            f"## ERRORS IN YOUR PREVIOUS ATTEMPT (fix these)\n"
            f"{error_text}\n\n"
            f"Your previous output:\n"
            f"```python\n{previous_content}\n```\n\n"
            f"Return ONLY valid JSON matching this schema:\n{_JSON_SCHEMA}\n"
        )
        return _make_messages(prompt)


def _extract_change_hint(reference_body: str) -> str:
    """Find the last return statement in the reference method."""
    output_line = ""
    try:
        wrapped = f"class _W:\n{reference_body}"
        ref_tree = ast.parse(wrapped)
        returns = [
            node for node in ast.walk(ref_tree) if isinstance(node, ast.Return) and node.value
        ]
        if returns:
            last_return = max(returns, key=lambda n: n.lineno)
            ref_lines = reference_body.split("\n")
            idx = last_return.lineno - 2  # -1 for 0-index, -1 for wrapper
            if 0 <= idx < len(ref_lines):
                output_line = ref_lines[idx].strip()
    except SyntaxError:
        pass

    if output_line:
        return (
            f"\nThe line that produces the final output is:\n"
            f"    {output_line}\n"
            f"This is the ONLY line you should change for the variant. "
            f"Keep everything above it identical.\n"
        )
    return ""
