# fitz_forge/planning/artifact/strategy.py
"""Pluggable artifact generation strategies.

Each strategy knows how to build a prompt and handle retries.
The generator picks the right strategy based on context.

All strategies output raw Python code — no JSON wrapping. The
filename and purpose are already known (passed in via context),
so there's no reason to ask the model to echo them back inside
a JSON object. This eliminates the entire class of quote-mangling
bugs from JSON extraction of embedded Python code.
"""

import ast
import logging
from typing import Any, Protocol

from fitz_forge.llm.generate import generate
from fitz_forge.planning.pipeline.stages.base import SYSTEM_PROMPT

from .context import ArtifactContext
from .validate import ArtifactError

logger = logging.getLogger(__name__)

_RAW_CODE_INSTRUCTION = (
    "Return ONLY the code (full implementation, not just signatures or stubs). "
    "No JSON wrapping. No markdown fences. No explanation. No prose."
)


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences without touching leading indentation.

    Surgical method rewrites are indented (4-space class-body indent). A naive
    `.strip()` removes that indent on the first line but leaves the rest,
    producing mixed-indent multi-method outputs that can't be parsed. So we
    strip only blank lines at the top/bottom and the fences themselves — never
    touching the leading whitespace of real code lines.
    """
    lines = raw.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("```"):
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines)


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
        prompt = self._build_prompt(ctx)

        safe = ctx.filename.replace("/", "_").replace("\\", "_")
        raw = await generate(
            client,
            messages=_make_messages(prompt),
            max_tokens=8192,
            label=f"artifact_surgical_{safe}",
        )
        return _strip_fences(raw)

    def _build_prompt(self, ctx: ArtifactContext) -> str:
        change_hint = _extract_change_hint(ctx.reference_method)
        grounding = _surgical_grounding_block(ctx)
        return (
            f"Rewrite this existing method to: {ctx.purpose}\n\n"
            f"## EXISTING METHOD (copy this structure exactly)\n"
            f"```python\n{ctx.reference_method}\n```\n"
            f"{grounding}\n"
            f"## INSTRUCTIONS\n"
            f"1. Copy the method above, renaming it appropriately\n"
            f"2. Keep ALL internal pipeline steps (every self._xxx call) "
            f"in the same order — these do retrieval, validation, "
            f"enrichment, etc. that must not be skipped\n"
            f"3. Do NOT skip steps or call lower-level primitives directly\n"
            f"4. Do NOT invent new self.xxx or self._xxx methods. Use ONLY "
            f"methods from METHODS AVAILABLE ON self above. If the right "
            f"helper doesn't exist, call only real methods — never guess "
            f"plausible-sounding names like self._execute_pipeline or "
            f"self._run_pipeline\n"
            f"5. Do NOT invent new Request/Response/Input/Output/Event/"
            f"Chunk/Stream/Message/Result classes for ANY position — "
            f"parameter annotations, return types, variable annotations, "
            f"yields, raises, isinstance, or instantiation. Use ONLY "
            f"classes from DATA MODEL FIELDS above, the reference method "
            f"above, or Python stdlib. If the right class doesn't exist, "
            f"yield primitives (str, dict, tuple) or compose from existing "
            f"dataclasses\n"
            f"6. When reading fields on a typed object, use ONLY the "
            f"field names listed under that class in DATA MODEL FIELDS\n"
            f"{change_hint}\n"
            f"{_RAW_CODE_INSTRUCTION}\n"
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

        base = self._build_prompt(ctx)
        prompt = (
            f"{base}\n"
            f"## ERRORS IN YOUR PREVIOUS ATTEMPT (fix these)\n"
            f"{error_text}\n\n"
            f"Your previous output:\n"
            f"```python\n{previous_content}\n```\n\n"
            f"{_RAW_CODE_INSTRUCTION}\n"
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
        return _strip_fences(raw)

    def _build_prompt(self, ctx: ArtifactContext) -> str:
        rules = (
            "Rules:\n"
            "- Write ONLY the new or modified code (not the entire file). "
            "Include the FULL method/function body with implementation logic, "
            "not just the signature or type declaration\n"
            "- Use exact attribute names from the source code above\n"
            "- When calling self.xxx or self._xxx (the target class's own "
            "methods), use ONLY methods listed in METHODS AVAILABLE ON self "
            "above. Do NOT invent new helper names — the retry feedback "
            "will keep rejecting plausible-sounding guesses\n"
            "- When calling self._xxx.method(), use ONLY methods listed "
            "in AVAILABLE METHODS ON INSTANCE ATTRIBUTES above\n"
            "- When calling imported objects (e.g. service.xxx()), use ONLY "
            "methods listed in IMPORTED TYPE APIs above. If a method is not "
            "listed, it does NOT exist — do NOT assume it will be added later\n"
            "- If the method you need does NOT exist on a dependency, "
            "compose the behavior from its existing methods instead of "
            "inventing new ones\n"
            "- When adding a parallel method, match the original "
            "method's parameters exactly\n"
            "- Do NOT fabricate method names — if unsure, omit the call\n"
            "- Do NOT invent new Request/Response/Input/Output/Event/Chunk/"
            "Stream/Message/Result/Payload/Context classes. Use ONLY classes "
            "that appear in DATA MODEL FIELDS above, AVAILABLE METHODS above, "
            "the CURRENT SOURCE CODE below, or Python stdlib. This rule "
            "applies to EVERY type position:\n"
            "    * parameter annotations     (def foo(req: X))\n"
            "    * return annotations        (def foo() -> X)\n"
            "    * variable annotations      (x: X = ...)\n"
            "    * instantiation             (X(...))\n"
            "    * yield values              (yield X(...))\n"
            "    * isinstance / cast         (isinstance(v, X))\n"
            "    * raise / except            (raise X, except X)\n"
            "  If the right class doesn't exist, compose from existing "
            "dataclasses, or yield primitives (str, dict, tuple) instead "
            "of inventing a new class\n"
            "- When reading fields on a typed object (parameter OR local "
            "variable, e.g. `request.foo`), use ONLY the field names listed "
            "under that class in DATA MODEL FIELDS. Do NOT invent new fields\n"
        )

        # Grounding block (high priority — goes first)
        grounding_parts = []
        if ctx.target_self_methods:
            grounding_parts.append(
                f"\n## METHODS AVAILABLE ON self (target class)\n"
                f"When calling self.xxx or self._xxx, use ONLY these — "
                f"do NOT invent new helper names:\n"
                f"```\n{ctx.target_self_methods}\n```"
            )
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
            f"Write code for: {ctx.filename}\n"
            f"Purpose: {ctx.purpose}\n\n"
            f"{rules}\n{grounding}\n\n"
            f"## RELEVANT DECISIONS\n{ctx.decisions}\n\n"
            f"{source_section}\n\n"
            f"{_RAW_CODE_INSTRUCTION}\n"
        )
        reasoning_budget = max(500, _TOKEN_BUDGET_CHARS - len(fixed) - 200)

        from fitz_forge.planning.pipeline.stages.synthesis import _truncate_at_line

        reasoning_final = _truncate_at_line(ctx.reasoning, reasoning_budget)

        return (
            f"Write code for: {ctx.filename}\n"
            f"Purpose: {ctx.purpose}\n\n"
            f"{rules}\n{grounding}\n\n"
            f"## RELEVANT DECISIONS\n{ctx.decisions}\n\n"
            f"{source_section}\n\n"
            f"## PLAN CONTEXT (background — lower priority than above)\n"
            f"{reasoning_final}\n\n"
            f"{_RAW_CODE_INSTRUCTION}\n"
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

        full_prompt = self._build_prompt(ctx)
        prompt = (
            f"{full_prompt}\n\n"
            f"## ERRORS IN YOUR PREVIOUS ATTEMPT (fix these)\n"
            f"{error_text}\n\n"
            f"Your previous output:\n"
            f"```python\n{previous_content}\n```\n\n"
            f"{_RAW_CODE_INSTRUCTION}\n"
        )
        return _make_messages(prompt)


def _surgical_grounding_block(ctx: ArtifactContext) -> str:
    """Compose DATA MODEL FIELDS + AVAILABLE METHODS for a surgical rewrite.

    Kept minimal on purpose — surgical's philosophy is "copy the reference,
    make the minimal change." But fabrication of schema classes is so common
    that the schema block pays for itself here too.
    """
    parts: list[str] = []
    if ctx.target_self_methods:
        parts.append(
            f"\n## METHODS AVAILABLE ON self (target class)\n"
            f"When calling self.xxx or self._xxx, use ONLY these — "
            f"do NOT invent new helper names:\n"
            f"```\n{ctx.target_self_methods}\n```\n"
        )
    if ctx.schema_fields:
        parts.append(
            f"\n## DATA MODEL FIELDS (use these exact class and field names)\n"
            f"{ctx.schema_fields}\n"
        )
    if ctx.available_methods:
        parts.append(
            f"\n## AVAILABLE METHODS ON INSTANCE ATTRIBUTES\n"
            f"When calling methods on self._xxx, use ONLY these:\n"
            f"{ctx.available_methods}\n"
        )
    return "".join(parts)


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
