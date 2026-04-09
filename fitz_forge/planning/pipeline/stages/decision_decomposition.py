# fitz_forge/planning/pipeline/stages/decision_decomposition.py
"""
Decision decomposition stage: one cheap LLM call to break the task into
atomic decisions.

Input: task description + call graph + one-line file summaries (NOT full source)
Output: ordered list of AtomicDecision objects with dependencies
"""

import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any

from fitz_forge.llm.generate import generate
from fitz_forge.planning.pipeline.stages.base import (
    PipelineStage,
    StageResult,
    extract_json,
)
from fitz_forge.planning.prompts import load_prompt
from fitz_forge.planning.schemas.decisions import (
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
        self,
        job_description: str,
        prior_outputs: dict[str, Any],
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
        decisions: list[dict],
        prior_outputs: dict[str, Any],
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

        interior = [n for n in call_graph.nodes if 0 < n.depth < max_depth]
        if len(interior) < 2:
            return ""

        decision_files: set[str] = {f for d in decisions for f in d.get("relevant_files", [])}

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

    @staticmethod
    def _score_decomposition(
        parsed: dict[str, Any],
        prior_outputs: dict[str, Any],
    ) -> tuple[float, dict[str, float]]:
        """Score a decomposition for best-of-2 selection.

        Criteria: decision count, call-graph coverage, question specificity,
        category diversity, dependency coherence.
        """
        decisions = parsed.get("decisions", [])
        breakdown: dict[str, float] = {}
        n = len(decisions)

        # Decision count: 8-18 is ideal
        if 8 <= n <= 18:
            breakdown["count"] = 10.0
        elif 5 <= n < 8 or 18 < n <= 25:
            breakdown["count"] = 5.0
        else:
            breakdown["count"] = 0.0

        # Call-graph coverage
        call_graph = prior_outputs.get("_call_graph")
        if call_graph and call_graph.nodes:
            graph_files = {nd.file_path for nd in call_graph.nodes}
            decision_files: set[str] = set()
            for d in decisions:
                for f in d.get("relevant_files", []):
                    decision_files.add(f)
            covered = 0
            for gf in graph_files:
                gf_base = os.path.basename(gf)
                for df in decision_files:
                    if gf in df or df in gf or os.path.basename(df) == gf_base:
                        covered += 1
                        break
            ratio = covered / len(graph_files) if graph_files else 0
            breakdown["graph_cov"] = ratio * 30.0
        else:
            breakdown["graph_cov"] = 15.0

        # Question specificity: concrete file/class refs in question text
        specific = 0
        for d in decisions:
            q = d.get("question", "")
            if re.search(r"[\w/]+\.py|[A-Z][a-z]+[A-Z]\w+|_\w+_", q):
                specific += 1
        breakdown["specificity"] = (specific / max(n, 1)) * 25.0

        # Category diversity
        categories = {d.get("category", "") for d in decisions}
        categories.discard("")
        breakdown["categories"] = min(len(categories) * 3.0, 15.0)

        # Dependency coherence
        ids = {d.get("id", "") for d in decisions}
        total_deps = sum(len(d.get("depends_on", [])) for d in decisions)
        valid_deps = sum(1 for d in decisions for dep in d.get("depends_on", []) if dep in ids)
        if total_deps > 0:
            breakdown["deps"] = (valid_deps / total_deps) * 15.0 + 5.0
        else:
            breakdown["deps"] = 5.0

        # Reference completeness: if a decision question mentions a class
        # name that exists in the structural index, the file containing
        # that class should be in relevant_files. Missing it means the
        # resolver won't see the definition and will guess wrong.
        agent_ctx = prior_outputs.get("_agent_context", {})
        index_text = agent_ctx.get("full_structural_index", "")
        if not index_text:
            index_text = prior_outputs.get("_gathered_context", "")
        if index_text:
            from fitz_forge.planning.validation.grounding import (
                StructuralIndexLookup,
            )

            lookup = StructuralIndexLookup(index_text)
            class_re = re.compile(r"`?([A-Z][a-zA-Z0-9]+(?:[A-Z][a-z]\w*)+)`?")
            total_refs = 0
            complete_refs = 0
            for d in decisions:
                question = d.get("question", "")
                rf = {f.replace("\\", "/") for f in d.get("relevant_files", [])}
                for m in class_re.finditer(question):
                    class_name = m.group(1)
                    cls = lookup.find_class(class_name)
                    if not cls:
                        continue
                    total_refs += 1
                    # Check if cls.file is covered by relevant_files
                    if any(
                        cls.file in f or f in cls.file
                        or os.path.basename(cls.file) == os.path.basename(f)
                        for f in rf
                    ):
                        complete_refs += 1
            if total_refs > 0:
                ratio = complete_refs / total_refs
                breakdown["ref_complete"] = ratio * 15.0
            else:
                breakdown["ref_complete"] = 15.0
        else:
            breakdown["ref_complete"] = 10.0

        return sum(breakdown.values()), breakdown

    async def execute(
        self,
        client: Any,
        job_description: str,
        prior_outputs: dict[str, Any],
    ) -> StageResult:
        try:
            messages = self.build_prompt(job_description, prior_outputs)
            await self._report_substep("decomposing")

            # Best-of-N with per-criterion quality gates: generate 2
            # candidates, pick the best.  If it fails ANY gate, generate
            # more (up to 4 total).  Each criterion must independently
            # clear its minimum — a high score in one can't compensate
            # for a failing score in another.
            _MIN_CANDIDATES = 2
            _MAX_CANDIDATES = 4
            _CRITERION_GATES: dict[str, float] = {
                "count": 5.0,           # must have reasonable decision count
                # graph_cov excluded: max achievable is ~6/30 with 13 decisions
                # referencing 1-3 files each vs 200-node call graph. The gate
                # is structurally impossible to clear.
                "specificity": 15.0,    # must reference specific files/classes
                "deps": 10.0,           # dependency refs must be valid
                "ref_complete": 10.0,   # mentioned classes must have their files
            }

            def _passes_gates(breakdown: dict[str, float]) -> tuple[bool, list[str]]:
                """Check if all criteria meet their minimums."""
                failures = []
                for criterion, minimum in _CRITERION_GATES.items():
                    val = breakdown.get(criterion, 0.0)
                    if val < minimum:
                        failures.append(f"{criterion}={val:.1f}<{minimum}")
                return len(failures) == 0, failures

            candidates: list[tuple[float, dict, str, dict]] = []
            last_error: Exception | None = None
            candidate_num = 0

            while candidate_num < _MAX_CANDIDATES:
                batch_size = _MIN_CANDIDATES if candidate_num == 0 else 1
                for _ in range(batch_size):
                    candidate_num += 1
                    try:
                        t0 = time.monotonic()
                        raw = await generate(
                            client, messages=messages,
                            temperature=0.3,
                            max_tokens=16384,
                            label=f"decomp_candidate_{candidate_num}",
                        )
                        t1 = time.monotonic()
                        logger.info(
                            f"Stage '{self.name}': candidate {candidate_num} took "
                            f"{t1 - t0:.1f}s ({len(raw)} chars)"
                        )
                        parsed = self.parse_output(raw)
                        score, breakdown = self._score_decomposition(
                            parsed,
                            prior_outputs,
                        )
                        candidates.append((score, parsed, raw, breakdown))
                        logger.info(
                            f"Stage '{self.name}': candidate {candidate_num} "
                            f"score={score:.1f} "
                            f"({len(parsed.get('decisions', []))} decisions) "
                            f"{breakdown}"
                        )
                    except Exception as e:
                        last_error = e
                        logger.warning(
                            f"Stage '{self.name}': candidate {candidate_num} failed: {e}"
                        )

                if not candidates:
                    if candidate_num >= _MAX_CANDIDATES:
                        break
                    continue

                # Check if best candidate passes ALL gates
                candidates.sort(key=lambda c: c[0], reverse=True)
                passed, failures = _passes_gates(candidates[0][3])
                if passed:
                    break
                if candidate_num >= _MAX_CANDIDATES:
                    break
                logger.info(
                    f"Stage '{self.name}': best candidate fails gates: "
                    f"{', '.join(failures)} — generating more"
                )

            if not candidates:
                raise last_error or RuntimeError(
                    f"All {candidate_num} decomposition candidates failed"
                )

            # Pick highest scoring candidate
            candidates.sort(key=lambda c: c[0], reverse=True)
            best_score, parsed, raw, _ = candidates[0]

            if len(candidates) > 1:
                margin = candidates[0][0] - candidates[1][0]
                logger.info(
                    f"Stage '{self.name}': selected candidate "
                    f"(score={best_score:.1f}, margin={margin:.1f}, "
                    f"{len(candidates)} candidates evaluated)"
                )

            decisions = parsed.get("decisions", [])

            # F1 fix: deduplicate decisions by question similarity
            keep_ids: set[str] = set()
            deduped: list[dict] = []
            for d in decisions:
                question = d.get("question", "").lower()
                is_dup = False
                for kept in deduped:
                    sim = SequenceMatcher(
                        None,
                        question,
                        kept.get("question", "").lower(),
                    ).ratio()
                    if sim >= 0.85:
                        is_dup = True
                        break
                if not is_dup:
                    deduped.append(d)
                    keep_ids.add(d.get("id", ""))
            if len(deduped) < len(decisions):
                logger.info(
                    f"Stage '{self.name}': deduped {len(decisions)} → {len(deduped)} decisions"
                )
                # Prune dangling depends_on refs
                for d in deduped:
                    d["depends_on"] = [dep for dep in d.get("depends_on", []) if dep in keep_ids]
                decisions = deduped
                parsed["decisions"] = decisions

            logger.info(f"Stage '{self.name}': produced {len(decisions)} decisions")

            # Coverage gate: retry once if interior call chain layers are skipped.
            coverage_hint = self._build_coverage_hint(decisions, prior_outputs)
            if coverage_hint:
                logger.info(f"Stage '{self.name}': coverage gap detected, retrying")
                retry_messages = self.build_prompt(
                    job_description,
                    prior_outputs,
                    coverage_hint=coverage_hint,
                )
                t0 = time.monotonic()
                retry_raw = await generate(
                    client, messages=retry_messages,
                    temperature=0,
                    max_tokens=16384,
                )
                t1 = time.monotonic()
                logger.info(f"Stage '{self.name}': coverage retry took {t1 - t0:.1f}s")
                try:
                    retry_parsed = self.parse_output(retry_raw)
                    retry_decisions = retry_parsed.get("decisions", [])
                    logger.info(
                        f"Stage '{self.name}': retry produced {len(retry_decisions)} decisions"
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
                bad_deps = [dep for dep in d.get("depends_on", []) if dep not in ids]
                if bad_deps:
                    logger.warning(f"Decision {d['id']}: removing invalid deps {bad_deps}")
                    d["depends_on"] = [dep for dep in d["depends_on"] if dep in ids]

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
