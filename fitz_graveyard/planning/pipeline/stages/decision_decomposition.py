# fitz_graveyard/planning/pipeline/stages/decision_decomposition.py
"""
Decision decomposition stage: one cheap LLM call to break the task into
atomic decisions.

Input: task description + call graph + one-line file summaries (NOT full source)
Output: ordered list of AtomicDecision objects with dependencies
"""

import logging
import os
import time
from typing import Any

from fitz_graveyard.planning.pipeline.stages.base import (
    PipelineStage,
    StageResult,
    extract_json,
)
from fitz_graveyard.planning.prompts import load_prompt
from fitz_graveyard.planning.schemas.decisions import (
    DecisionDecompositionOutput,
)

logger = logging.getLogger(__name__)


class DecisionDecompositionStage(PipelineStage):
    """Break a planning task into atomic, ordered decisions.

    Uses:
    - Task description (from user)
    - Call graph (from call_graph.py -- deterministic AST extraction)
    - One-line file summaries (from agent manifest -- NOT full source)

    Output: list of AtomicDecision with depends_on ordering.

    Token budget: ~2-4K input (task + call graph + manifest).
    This is a CHEAP call -- the model isn't reasoning about architecture,
    just identifying what questions need answering.
    """

    @property
    def name(self) -> str:
        return "decision_decomposition"

    @property
    def progress_range(self) -> tuple[float, float]:
        return (0.10, 0.20)

    def build_prompt(
        self, job_description: str, prior_outputs: dict[str, Any],
        coverage_hint: str = "",
    ) -> list[dict]:
        call_graph_text = prior_outputs.get("_call_graph_text", "")
        manifest = prior_outputs.get("_raw_summaries", "")
        impl_check = self._get_implementation_check(prior_outputs)

        prompt_template = load_prompt("decision_decomposition")
        prompt = prompt_template.format(
            task_description=job_description,
            call_graph=call_graph_text,
            file_manifest=manifest,
        )
        if impl_check:
            prompt = f"{impl_check}\n\n{prompt}"
        if coverage_hint:
            prompt = f"{prompt}\n\n{coverage_hint}"
        return self._make_messages(prompt)

    @staticmethod
    def _build_coverage_hint(
        decisions: list[dict], prior_outputs: dict[str, Any],
    ) -> str:
        """Return a retry hint if interior call chain layers are uncovered.

        Checks whether decisions collectively cover the intermediate nodes
        (depth > 0, depth < max_depth) in the call graph. If more than half
        are missing, the decomposition took a shortcut — return a hint listing
        the uncovered files so the model can retry with explicit guidance.
        """
        call_graph = prior_outputs.get("_call_graph")
        if not call_graph or not call_graph.nodes:
            return ""
        max_depth = call_graph.max_depth
        if max_depth < 2:
            return ""

        interior = [
            n for n in call_graph.nodes
            if 0 < n.depth < max_depth
        ]
        if len(interior) < 2:
            return ""

        decision_files: set[str] = {
            f for d in decisions for f in d.get("relevant_files", [])
        }

        def is_covered(path: str) -> bool:
            base = os.path.basename(path)
            for df in decision_files:
                if path == df or path in df or df in path:
                    return True
                if os.path.basename(df) == base:
                    return True
            return False

        uncovered = [n for n in interior if not is_covered(n.file_path)]
        if not uncovered or len(uncovered) * 2 < len(interior):
            return ""

        file_list = "\n".join(f"- {n.file_path}" for n in uncovered[:6])
        return (
            "COVERAGE WARNING: Your decomposition skips intermediate call chain "
            "layers. These files are in the call chain but have no decisions:\n"
            f"{file_list}\n"
            "Every layer that changes needs at least one decision with that "
            "file in relevant_files."
        )

    def parse_output(self, raw_output: str) -> dict[str, Any]:
        data = extract_json(raw_output)
        output = DecisionDecompositionOutput(**data)
        return output.model_dump()

    async def execute(
        self,
        client: Any,
        job_description: str,
        prior_outputs: dict[str, Any],
    ) -> StageResult:
        try:
            messages = self.build_prompt(job_description, prior_outputs)
            await self._report_substep("decomposing")
            t0 = time.monotonic()
            raw = await client.generate(
                messages=messages, temperature=0, max_tokens=4096,
            )
            t1 = time.monotonic()
            logger.info(
                f"Stage '{self.name}': decomposition took "
                f"{t1 - t0:.1f}s ({len(raw)} chars)"
            )

            parsed = self.parse_output(raw)
            decisions = parsed.get("decisions", [])
            logger.info(
                f"Stage '{self.name}': produced {len(decisions)} decisions"
            )

            # Coverage gate: retry once if interior call chain layers are skipped.
            coverage_hint = self._build_coverage_hint(decisions, prior_outputs)
            if coverage_hint:
                logger.info(
                    f"Stage '{self.name}': coverage gap detected, retrying"
                )
                retry_messages = self.build_prompt(
                    job_description, prior_outputs, coverage_hint=coverage_hint,
                )
                t0 = time.monotonic()
                retry_raw = await client.generate(
                    messages=retry_messages, temperature=0, max_tokens=4096,
                )
                t1 = time.monotonic()
                logger.info(
                    f"Stage '{self.name}': coverage retry took {t1 - t0:.1f}s"
                )
                try:
                    retry_parsed = self.parse_output(retry_raw)
                    retry_decisions = retry_parsed.get("decisions", [])
                    logger.info(
                        f"Stage '{self.name}': retry produced "
                        f"{len(retry_decisions)} decisions"
                    )
                    parsed = retry_parsed
                    decisions = retry_decisions
                    raw = retry_raw
                except Exception as e:
                    logger.warning(
                        f"Stage '{self.name}': coverage retry parse failed "
                        f"({e}), using original decisions"
                    )

            # Validate dependency references
            ids = {d["id"] for d in decisions}
            for d in decisions:
                bad_deps = [
                    dep for dep in d.get("depends_on", [])
                    if dep not in ids
                ]
                if bad_deps:
                    logger.warning(
                        f"Decision {d['id']}: removing invalid deps {bad_deps}"
                    )
                    d["depends_on"] = [
                        dep for dep in d["depends_on"]
                        if dep in ids
                    ]

            return StageResult(
                stage_name=self.name,
                success=True,
                output=parsed,
                raw_output=raw,
            )
        except Exception as e:
            logger.error(f"Stage '{self.name}' failed: {e}", exc_info=True)
            return StageResult(
                stage_name=self.name,
                success=False,
                output={},
                raw_output="",
                error=str(e),
            )
