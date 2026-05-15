# fitz_forge/planning/agent/gatherer.py
"""
AgentContextGatherer — powered by KRAG's retrieve() API.

Core retrieval (codebase ingestion, LLM file selection, import/neighbor
expansion, compression) is delegated to fitz-sage's KRAG engine via its public
``retrieve()`` method.  This module adds planning-specific post-processing:

  - Interface / library signature extraction
  - Seed-and-fetch context delivery
  - File priority ordering
  - Provenance tracking
  - Planning-optimised compression (test file body collapse)

One retrieval engine, centrally maintained in fitz-sage.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fitz_sage.core import Query
from fitz_sage.engines.fitz_krag.config.schema import FitzKragConfig
from fitz_sage.runtime.runner import create_engine

from fitz_forge.planning.agent.compressor import compress_file
from fitz_forge.planning.agent.indexer import (
    build_structural_index,
    extract_interface_signatures,
    extract_library_signatures,
)

if TYPE_CHECKING:
    from fitz_forge.config.schema import AgentConfig

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SEED_FILES = 50


class AgentContextGatherer:
    """Retrieval pipeline powered by fitz-sage's KRAG engine.

    Ingests the target codebase into a KRAG collection, runs KRAG's
    retrieve() for the files relevant to the job, then adds planning-specific
    post-processing.
    """

    def __init__(self, config: "AgentConfig", source_dir: str) -> None:
        self._config = config
        self._source_dir = source_dir

    async def gather(
        self,
        client: Any,
        job_description: str,
        progress_callback: Callable[[float, str], None] | None = None,
        override_files: list[str] | None = None,
    ) -> dict[str, str]:
        """Run KRAG retrieval and return context dict.

        Ingests the codebase into a KRAG collection (AST symbol indexing,
        no LLM), then calls KRAG's retrieve() for the files relevant to the
        job description.

        Args:
            client: fitz-forge LLM client — supplies the chat endpoint KRAG uses.
            job_description: Natural language task description.
            progress_callback: Optional progress reporter.
            override_files: If set, skip LLM retrieval and use these
                file paths directly. All post-processing (compress,
                structural overview, seed splitting, provenance) runs
                identically. Used by benchmarks to isolate reasoning.

        Returns:
            Dict with "synthesized", "raw_summaries", "file_contents",
            and "agent_files".  Empty strings on failure.
        """
        empty: dict[str, Any] = {"synthesized": "", "raw_summaries": ""}

        if not self._config.enabled:
            logger.info("Agent context gathering disabled by config")
            return empty

        t_pipeline = time.monotonic()

        try:
            # Step 1: Ingest the codebase into a KRAG collection (AST symbol
            # indexing, no LLM) and build the engine pointed at fitz-forge's LLM.
            await self._report(progress_callback, 0.06, "agent:mapping")
            engine, manifest = await asyncio.to_thread(self._build_engine, client)
            file_paths = sorted(manifest.entries())

            if override_files is not None:
                # Benchmark mode: skip LLM retrieval, use fixed file list
                results = self._override_results(override_files)
                logger.info(
                    f"AgentContextGatherer: using {len(results)} override files "
                    f"(skipped LLM retrieval)"
                )
            else:
                # Normal mode: KRAG retrieval
                await self._report(progress_callback, 0.065, "agent:scanning_index")
                results = await asyncio.to_thread(
                    engine.retrieve, Query(text=job_description)
                )

            # KRAG can surface the same file from multiple strategies — keep
            # one ReadResult per file, preserving retrieval order.
            seen_paths: set[str] = set()
            deduped = []
            for r in results:
                if r.file_path not in seen_paths:
                    seen_paths.add(r.file_path)
                    deduped.append(r)
            results = deduped

            if not results:
                logger.warning("AgentContextGatherer: retrieval returned no results")
                return empty

            logger.info(
                f"AgentContextGatherer: KRAG retrieved {len(results)} files "
                f"from {len(file_paths)} indexed"
            )

            # Step 3: Categorize by origin
            await self._report(progress_callback, 0.070, "agent:import_expand")
            scan_hits: list[str] = []
            import_added: list[str] = []
            hub_added: list[str] = []
            neighbor_added: list[str] = []

            for r in results:
                origin = r.address.metadata.get("origin", "neighbor")
                if origin == "selected":
                    scan_hits.append(r.file_path)
                elif origin == "import":
                    import_added.append(r.file_path)
                elif origin == "hub":
                    hub_added.append(r.file_path)
                elif origin == "neighbor":
                    neighbor_added.append(r.file_path)

            included = [r.file_path for r in results]

            logger.info(
                f"AgentContextGatherer: {len(scan_hits)} selected, "
                f"{len(import_added)} import, {len(hub_added)} hub, "
                f"{len(neighbor_added)} neighbor"
            )

            # Step 4: Build file_contents with planning compression
            await self._report(progress_callback, 0.075, "agent:neighbor_expand")
            file_contents: dict[str, str] = {}
            raw_chars = 0
            for r in results:
                raw_chars += len(r.content)
                # Apply planning-specific compression (test body collapse,
                # non-Python comment stripping).  Python AST compression
                # was already applied by KRAG retrieval.
                file_contents[r.file_path] = compress_file(r.content, r.file_path)

            comp_chars = sum(len(c) for c in file_contents.values())
            if raw_chars > 0:
                logger.info(
                    f"AgentContextGatherer: compressed {len(file_contents)} files "
                    f"({raw_chars} -> {comp_chars} chars, "
                    f"{100 * (1 - comp_chars / raw_chars):.0f}% reduction)"
                )

            # Step 5: Build structural overview
            await self._report(progress_callback, 0.080, "agent:reading")
            selected_index = build_structural_index(
                str(Path(self._source_dir)),
                included,
                max_file_bytes=self._config.max_file_bytes,
            )
            signatures = extract_interface_signatures(
                self._source_dir,
                included,
                self._config.max_file_bytes,
            )
            lib_sigs = extract_library_signatures(
                self._source_dir,
                included,
                file_paths,
                self._config.max_file_bytes,
            )

            overview_parts: list[str] = []
            if signatures:
                overview_parts.append(
                    "--- INTERFACE SIGNATURES (auto-extracted, ground truth) ---\n" + signatures
                )
            if lib_sigs:
                overview_parts.append(
                    "--- LIBRARY API REFERENCE (installed packages, ground truth) ---\n" + lib_sigs
                )
            overview_parts.append(
                "--- STRUCTURAL OVERVIEW (all selected files) ---\n" + selected_index
            )
            structural_overview = "\n\n".join(overview_parts)

            # Step 6: Context delivery (manifest + inspect_files tool)
            #
            # A/B testing showed:
            # - Inline seed source is noise (~5K tokens the model ignores)
            # - Full structural index in prompt is load-bearing but expensive
            # - One-liner manifest + inspect_files tool saves ~4K tokens with
            #   zero quality regression (10/10 consistency, 40% faster)
            #
            # raw_summaries gets: signatures + one-liner manifest
            # Structural detail served on-demand via inspect_files tool

            # Parse per-file entries from structural index
            file_index_entries: dict[str, str] = {}
            for path in included:
                marker = f"## {path}\n"
                idx = selected_index.find(marker)
                if idx >= 0:
                    entry_start = idx + len(marker)
                    entry_end = selected_index.find("\n## ", entry_start)
                    entry = (
                        selected_index[entry_start:entry_end].strip()
                        if entry_end > 0
                        else selected_index[entry_start:].strip()
                    )
                    file_index_entries[path] = entry

            # Build one-liner manifest (path + docstring)
            manifest_lines = []
            for path in included:
                entry = file_index_entries.get(path, "")
                lines = entry.split("\n") if entry else []
                doc_line = next(
                    (line.strip() for line in lines if line.strip().startswith("doc:")),
                    "",
                )
                manifest_lines.append(f"  {path} — {doc_line}" if doc_line else f"  {path}")

            raw_parts: list[str] = []
            if signatures:
                raw_parts.append(
                    "--- INTERFACE SIGNATURES (auto-extracted, ground truth) ---\n" + signatures
                )
            if lib_sigs:
                raw_parts.append(
                    "--- LIBRARY API REFERENCE (installed packages, ground truth) ---\n" + lib_sigs
                )
            raw_parts.append(
                f"--- FILE MANIFEST ({len(included)} files) ---\n" + "\n".join(manifest_lines)
            )
            raw_summaries = "\n\n".join(raw_parts)

            # All files go into the tool pool
            seed_files: list[str] = []
            tool_pool_files: list[str] = [p for p in included if p in file_contents]
            logger.info(
                f"AgentContextGatherer: {len(tool_pool_files)} files "
                f"in tool pool ({sum(len(file_contents.get(p, '')) for p in tool_pool_files)} chars), "
                f"manifest={len(manifest_lines)} entries, "
                f"index_entries={len(file_index_entries)}"
            )

            # Step 7: Build provenance
            seed_set = set(seed_files)
            scan_set_prov = set(scan_hits)
            import_set = set(import_added)
            hub_set = set(hub_added)
            neighbor_set = set(neighbor_added)

            file_provenance: dict[str, dict] = {}
            for path in included:
                signals: list[str] = []
                if path in scan_set_prov:
                    signals.append("scan")
                if path in import_set:
                    signals.append("import")
                if path in hub_set:
                    signals.append("hub")
                if path in neighbor_set:
                    signals.append("neighbor")
                file_provenance[path] = {
                    "signals": signals,
                    "in_prompt": path in seed_set,
                }

            t_total = time.monotonic() - t_pipeline
            logger.info(
                f"AgentContextGatherer: {len(included)} files "
                f"({comp_chars} chars compressed, "
                f"{len(structural_overview)} chars overview) — "
                f"pipeline total {t_total:.1f}s"
            )

            # Build full structural index (all files, not just selected)
            # for artifact duplicate checking downstream
            full_index = build_structural_index(
                str(Path(self._source_dir)),
                file_paths,
                max_file_bytes=self._config.max_file_bytes,
            )

            # Build untruncated validation index (includes Pydantic fields
            # for typed attribute validation).
            validation_index = build_structural_index(
                str(Path(self._source_dir)),
                file_paths,
                max_file_bytes=self._config.max_file_bytes,
                max_chars=0,  # 0 = no truncation
            )

            return {
                "synthesized": structural_overview,
                "raw_summaries": raw_summaries,
                "file_contents": file_contents,
                "file_index_entries": file_index_entries,
                "full_structural_index": full_index,
                "validation_index": validation_index,
                "agent_files": {
                    "total_screened": len(file_paths),
                    "all_files": file_paths,
                    "scan_hits": scan_hits,
                    "selected": included,
                    "included": included,
                    "forward_map": {},
                    "reverse_count": {},
                    "file_provenance": file_provenance,
                },
            }

        except Exception:
            logger.exception("AgentContextGatherer: pipeline failed")
            return empty

    # ------------------------------------------------------------------
    # KRAG engine
    # ------------------------------------------------------------------

    def _build_engine(self, client: Any) -> tuple[Any, Any]:
        """Create a KRAG engine pointed at fitz-forge's LLM and ingest the codebase.

        Ingestion (``point``) is AST-only — fast, no LLM. The collection is
        keyed by source directory, so re-planning the same codebase reuses the
        index. Returns ``(engine, manifest)``. Synchronous — call via
        ``asyncio.to_thread``.
        """
        source = Path(self._source_dir).resolve()
        collection = "fitz_forge_" + hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
        # fitz-forge's LLM clients expose ``base_url`` as an OpenAI-compatible
        # /v1 endpoint — KRAG's ``endpoint`` provider consumes it directly.
        model_spec = f"endpoint/{client.model}"
        config = FitzKragConfig(
            collection=collection,
            chat_fast=model_spec,
            chat_balanced=model_spec,
            chat_smart=model_spec,
            chat_base_url=client.base_url,
            # One local model serves one request at a time — concurrent
            # retrieval strategies just thrash its KV cache.
            retrieval_workers=1,
            # Single retrieval pass — planning queries don't need KRAG's
            # iterative multi-hop refinement, and each hop is another full
            # round of (serialized) LLM calls.
            enable_multi_hop=False,
        )
        engine = create_engine("fitz_krag", config=config)
        manifest = engine.point(source, collection=collection, start_worker=False)
        return engine, manifest

    def _override_results(self, override_files: list[str]) -> list:
        """Build ReadResults from a fixed file list — benchmark mode, no retrieval."""
        from fitz_sage.engines.fitz_krag.types import Address, AddressKind, ReadResult

        src = Path(self._source_dir)
        results: list = []
        for rel in override_files:
            full = src / rel
            if not full.is_file():
                continue
            try:
                raw = full.read_bytes()[: self._config.max_file_bytes]
                content = raw.decode("utf-8", errors="replace")
            except OSError:
                continue
            results.append(
                ReadResult(
                    address=Address(
                        kind=AddressKind.FILE,
                        source_id=rel,
                        location=rel,
                        summary=rel,
                        score=1.0,
                        metadata={"origin": "selected"},
                    ),
                    content=content,
                    file_path=rel,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prioritize_for_summary(paths: list[str]) -> list[str]:
        """Sort paths so code files come before docs/tests/examples.

        Priority tiers (lower = summarized first):
          0: source code (.py under the main package)
          1: config/build files (.yaml, .toml, .cfg, etc.)
          2: tests
          3: everything else (docs, examples, tools, .md, etc.)

        Within each tier, original order is preserved.
        """
        _DOC_DIRS = {"docs", "examples", "tools", ".fitz-forge", ".github"}
        _TEST_DIRS = {"tests", "test"}

        def _tier(p: str) -> int:
            first_dir = p.split("/")[0] if "/" in p else ""
            if first_dir in _TEST_DIRS:
                return 2
            if first_dir in _DOC_DIRS or p.endswith(".md"):
                return 3
            if p.endswith(".py"):
                return 0
            return 1

        return sorted(paths, key=_tier)

    @staticmethod
    async def _report(
        callback: Callable[[float, str], None] | None,
        progress: float,
        phase: str,
    ) -> None:
        """Report progress, handling both sync and async callbacks."""
        if not callback:
            return
        result = callback(progress, phase)
        if hasattr(result, "__await__"):
            await result
