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

from .closure import (
    ClosureViolation,
    check_closure,
    route_missing_symbol,
)
from .context import assemble_context
from .strategy import (
    ArtifactStrategy,
    NewCodeStrategy,
    SurgicalRewriteStrategy,
    _strip_fences,
)
from .validate import ArtifactError, validate

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


# ---------------------------------------------------------------------------
# Batch-level black box: generate_artifact_set
# ---------------------------------------------------------------------------


@dataclass
class ArtifactSetResult:
    """Output of the batch-level black box.

    `results` is always returned. `closed=True` means the closure invariant
    holds over the final set. `closure_violations` lists remaining unsatisfied
    references when `closed=False`.
    """

    results: list[ArtifactResult]
    closed: bool = True
    closure_violations: list[ClosureViolation] = field(default_factory=list)
    repair_iterations: int = 0
    expanded_files: list[str] = field(default_factory=list)  # files added by repair

    def as_artifact_dicts(self) -> list[dict[str, Any]]:
        """Convert successful results to the {filename, content, purpose} format
        synthesis expects."""
        return [
            {"filename": r.filename, "content": r.content, "purpose": r.purpose}
            for r in self.results
            if r.success
        ]


async def generate_artifact_set(
    client: Any,
    specs: list[tuple[str, str]],  # list of (filename, purpose)
    source_dir: str,
    structural_index: str,
    decisions_for: Any,  # Callable[[str], str] — filename -> relevant decisions text
    reasoning: str,
    prior_outputs: dict[str, Any] | None = None,
    max_repair_iters: int = 2,
) -> ArtifactSetResult:
    """Batch-level black box. Generate a closed, implementable artifact set.

    Internally calls `generate_artifact` for each spec, then runs the plan-level
    closure check. If any cross-file reference is unsatisfied, attempts to
    repair by either:
      1. Expanding the set — generate a new artifact for the file that should
         own the missing symbol (e.g. `services/fitz_service.py` to add
         `query_stream` when a route artifact references it).
      2. (V1: not yet) Regenerating the violating artifact with feedback.

    Returns an ArtifactSetResult with the full set, closure status, and any
    remaining violations.

    Synthesis stage uses `result.as_artifact_dicts()` to get the final clean
    set in its existing format.
    """
    from fitz_forge.planning.validation.grounding import StructuralIndexLookup

    results: list[ArtifactResult] = []
    prior_sigs: list[str] = []

    # Phase 1 — per-artifact generation. The per-artifact black box is unchanged.
    for filename, purpose in specs:
        relevant_decisions = (
            decisions_for(filename) if callable(decisions_for) else str(decisions_for or "")
        )
        result = await generate_artifact(
            client=client,
            filename=filename,
            purpose=purpose,
            source_dir=source_dir,
            structural_index=structural_index,
            decisions=relevant_decisions,
            reasoning=reasoning,
            prior_outputs=prior_outputs,
            prior_sigs=prior_sigs if prior_sigs else None,
        )
        results.append(result)
        if result.success:
            prior_sigs.extend(result.signatures)

    # Phase 2 — closure check + repair loop.
    # Build the lookup once (augmentation is expensive) and reuse it.
    lookup = StructuralIndexLookup(structural_index)
    if source_dir:
        lookup.augment_from_source_dir(source_dir)

    expanded_files: list[str] = []
    violations: list[ClosureViolation] = []

    for iter_idx in range(max_repair_iters + 1):
        artifact_dicts = [
            {"filename": r.filename, "content": r.content, "purpose": r.purpose}
            for r in results
            if r.success
        ]
        violations = check_closure(artifact_dicts, lookup, source_dir=source_dir)

        if not violations:
            if iter_idx > 0:
                logger.info(
                    "artifact_set: closure reached after %d repair iteration(s)",
                    iter_idx,
                )
            return ArtifactSetResult(
                results=results,
                closed=True,
                closure_violations=[],
                repair_iterations=iter_idx,
                expanded_files=expanded_files,
            )

        if iter_idx >= max_repair_iters:
            # Out of repair budget — return with unclosed set.
            break

        logger.info(
            "artifact_set: closure check found %d violation(s), attempting repair (iter %d)",
            len(violations),
            iter_idx + 1,
        )

        # Repair strategy 1: expand the set for "missing" violations. Group
        # missing symbols by target file so one repair artifact can cover
        # multiple missing methods on the same class.
        to_expand: dict[str, list[ClosureViolation]] = {}
        # Repair strategy 2: regenerate violators for usage/kwargs/field
        # violations. Group violations by the offending artifact filename.
        to_regenerate: dict[str, list[ClosureViolation]] = {}

        for v in violations:
            if v.kind == "missing":
                target = route_missing_symbol(v, lookup)
                if (
                    target
                    and not any(r.filename == target for r in results)
                    and target not in expanded_files
                ):
                    to_expand.setdefault(target, []).append(v)
            elif v.kind in ("usage", "kwargs", "field", "import"):
                to_regenerate.setdefault(v.artifact, []).append(v)

        if not to_expand and not to_regenerate:
            logger.info("artifact_set: no routable repair targets, returning unclosed set")
            break

        # Strategy 1: expand the set with new repair artifacts.
        for target, vs in to_expand.items():
            symbols = ", ".join(sorted({v.ref.pretty() for v in vs}))
            repair_purpose = (
                f"Add the following method(s) referenced by sibling artifacts "
                f"but missing from the existing code: {symbols}. "
                f"Preserve all existing code in the file."
            )
            logger.info(
                "artifact_set: expanding set with %s (missing: %s)",
                target,
                symbols,
            )
            repair_decisions = (
                decisions_for(target) if callable(decisions_for) else str(decisions_for or "")
            )
            repair_result = await generate_artifact(
                client=client,
                filename=target,
                purpose=repair_purpose,
                source_dir=source_dir,
                structural_index=structural_index,
                decisions=repair_decisions,
                reasoning=reasoning,
                prior_outputs=prior_outputs,
                prior_sigs=prior_sigs if prior_sigs else None,
            )
            results.append(repair_result)
            expanded_files.append(target)
            if repair_result.success:
                prior_sigs.extend(repair_result.signatures)
            else:
                logger.warning(
                    "artifact_set: repair artifact %s failed — %s",
                    target,
                    repair_result.failure_reason,
                )

        # Strategy 2: regenerate violating artifacts with feedback about the
        # specific usage/kwargs/field errors.
        for offender_file, vs in to_regenerate.items():
            # Find the existing result to regenerate
            offender_idx = next(
                (i for i, r in enumerate(results) if r.filename == offender_file),
                None,
            )
            if offender_idx is None:
                continue
            old_result = results[offender_idx]
            feedback_lines = [f"- {v.ref.pretty()}: {v.detail}" for v in vs[:5]]
            feedback = "\n".join(feedback_lines)
            regen_purpose = (
                f"{old_result.purpose}\n\n"
                f"## Fix these cross-artifact issues from the previous attempt:\n"
                f"{feedback}"
            )
            logger.info(
                "artifact_set: regenerating %s with %d usage/kwargs feedback item(s)",
                offender_file,
                len(vs),
            )
            regen_decisions = (
                decisions_for(offender_file)
                if callable(decisions_for)
                else str(decisions_for or "")
            )
            regen_result = await generate_artifact(
                client=client,
                filename=offender_file,
                purpose=regen_purpose,
                source_dir=source_dir,
                structural_index=structural_index,
                decisions=regen_decisions,
                reasoning=reasoning,
                prior_outputs=prior_outputs,
                prior_sigs=prior_sigs if prior_sigs else None,
            )
            if regen_result.success:
                results[offender_idx] = regen_result
            else:
                logger.warning(
                    "artifact_set: regeneration of %s failed — %s",
                    offender_file,
                    regen_result.failure_reason,
                )

    # Exhausted repair budget or had no routable repairs.
    logger.warning(
        "artifact_set: returning unclosed set (%d violation(s) remain after %d iter(s))",
        len(violations),
        max_repair_iters,
    )
    for v in violations[:10]:
        logger.warning("artifact_set: unclosed: %s", v.pretty())
    return ArtifactSetResult(
        results=results,
        closed=False,
        closure_violations=violations,
        repair_iterations=max_repair_iters,
        expanded_files=expanded_files,
    )
