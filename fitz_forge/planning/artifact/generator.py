# fitz_forge/planning/artifact/generator.py
"""Artifact generation black box.

Input goes in, gets validated internally, clean artifact comes out.
If the LLM produces bad output, retries with specific error feedback.
After max_attempts, returns a failure with the reason.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from fitz_forge.llm.generate import generate

from .context import assemble_context
from .strategy import (
    ArtifactStrategy,
    NewCodeStrategy,
    SurgicalRewriteStrategy,
    _strip_fences,
)
from .validate import ArtifactError, _fix_docstring_quotes, validate

logger = logging.getLogger(__name__)


@dataclass
class ArtifactResult:
    """Output of the artifact generation black box."""

    filename: str
    content: str  # empty on failure
    purpose: str
    success: bool
    failure_reason: str = ""  # why it failed (after all retries)
    attempts: int = 1
    strategy: str = ""  # "surgical" or "new_code"
    signatures: list[str] = field(default_factory=list)  # for downstream artifacts
    errors: list[ArtifactError] = field(default_factory=list)  # last validation errors


def _extract_signatures(content: str, filename: str) -> list[str]:
    """Extract method signatures for downstream artifact consistency."""
    from fitz_forge.planning.pipeline.stages.synthesis import (
        _extract_method_signatures,
    )

    return _extract_method_signatures(content, filename)


async def generate_artifact(
    client: Any,
    filename: str,
    purpose: str,
    source_dir: str,
    structural_index: str,
    decisions: str,
    reasoning: str,
    prior_outputs: dict[str, Any] | None = None,
    prior_sigs: list[str] | None = None,
    max_attempts: int = 3,
) -> ArtifactResult:
    """Black box: produce a validated artifact or a failure reason.

    1. Assemble context (deterministic)
    2. Pick strategy (surgical if reference method exists, else new code)
    3. Generate via strategy
    4. Validate output
    5. Retry with error feedback if validation fails
    6. Return clean artifact or explicit failure
    """
    # 1. Assemble context
    ctx = assemble_context(
        filename=filename,
        purpose=purpose,
        source_dir=source_dir,
        structural_index=structural_index,
        decisions=decisions,
        reasoning=reasoning,
        prior_outputs=prior_outputs,
        prior_sigs=prior_sigs,
    )

    # 2. Pick strategy
    strategy: ArtifactStrategy
    if ctx.reference_method:
        strategy = SurgicalRewriteStrategy()
    else:
        strategy = NewCodeStrategy()

    logger.info(
        "artifact[%s]: strategy=%s, source=%d chars, ref=%d chars",
        filename,
        strategy.name,
        len(ctx.compressed_source),
        len(ctx.reference_method),
    )

    # 3-5. Generate + validate + retry loop
    content = ""
    errors: list[ArtifactError] = []
    hard_errors: list[ArtifactError] = []

    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                content = await strategy.generate(client, ctx)
            else:
                # Retry with error feedback
                retry_messages = strategy.build_retry_prompt(ctx, content, hard_errors)
                safe = filename.replace("/", "_").replace("\\", "_")
                raw = await generate(
                    client,
                    messages=retry_messages,
                    max_tokens=8192,
                    label=f"artifact_retry{attempt}_{safe}",
                )
                content = _strip_fences(raw)
        except Exception as e:
            logger.warning(
                "artifact[%s]: attempt %d failed: %s",
                filename,
                attempt + 1,
                e,
            )
            if attempt == max_attempts - 1:
                return ArtifactResult(
                    filename=filename,
                    content="",
                    purpose=purpose,
                    success=False,
                    failure_reason=f"Generation failed: {e}",
                    attempts=attempt + 1,
                    strategy=strategy.name,
                )
            continue

        if not content:
            logger.warning(
                "artifact[%s]: attempt %d returned empty content",
                filename,
                attempt + 1,
            )
            if attempt == max_attempts - 1:
                return ArtifactResult(
                    filename=filename,
                    content="",
                    purpose=purpose,
                    success=False,
                    failure_reason="All attempts returned empty content",
                    attempts=attempt + 1,
                    strategy=strategy.name,
                )
            continue

        # Sanitize: fix docstring quote mangling from JSON extraction
        content = _fix_docstring_quotes(content)

        # Validate
        errors = validate(content, ctx)

        # Filter to hard errors only (skip not_implemented soft fail)
        hard_errors = [e for e in errors if e.check != "not_implemented"]

        if not hard_errors:
            # Success
            sigs = _extract_signatures(content, filename)
            logger.info(
                "artifact[%s]: success on attempt %d (%s, %d chars, %d sigs)",
                filename,
                attempt + 1,
                strategy.name,
                len(content),
                len(sigs),
            )
            return ArtifactResult(
                filename=filename,
                content=content,
                purpose=purpose,
                success=True,
                attempts=attempt + 1,
                strategy=strategy.name,
                signatures=sigs,
                errors=[e for e in errors if e.check == "not_implemented"],
            )

        # Log errors and retry
        error_summary = "; ".join(f"{e.check}: {e.message[:60]}" for e in hard_errors[:3])
        if attempt < max_attempts - 1:
            logger.info(
                "artifact[%s]: attempt %d has %d error(s), retrying: %s",
                filename,
                attempt + 1,
                len(hard_errors),
                error_summary,
            )
        else:
            logger.warning(
                "artifact[%s]: exhausted %d attempts, %d error(s) remain: %s",
                filename,
                max_attempts,
                len(hard_errors),
                error_summary,
            )

    # All attempts exhausted — return best effort with errors
    return ArtifactResult(
        filename=filename,
        content=content,
        purpose=purpose,
        success=False,
        failure_reason=f"{len(hard_errors)} validation error(s) after {max_attempts} attempts",
        attempts=max_attempts,
        strategy=strategy.name,
        errors=errors,
    )
