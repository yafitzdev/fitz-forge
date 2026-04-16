# Agent Context Gathering

## Problem

Planning stages need codebase awareness to produce grounded architectural plans, but
dumping the entire codebase into an LLM prompt is impossible (codebases are hundreds of
thousands of tokens) and naive truncation loses the most important files. The system
needs to select, prioritize, and compress source code into a form that small local LLMs
can consume while preserving the structural signals that matter for planning.

## Solution

A pre-stage retrieval pipeline powered by fitz-sage's `CodeRetriever`. The gatherer
bridges fitz-forge's async LLM client to fitz-sage's synchronous interface, runs
multi-signal file selection (LLM structural scan, import expansion, hub/facade
expansion, neighbor expansion), then applies planning-specific post-processing:
interface signature extraction, library signature extraction, and token-efficient
compression. The result is delivered as a one-liner file manifest plus on-demand
`inspect_files`/`read_file` tools rather than inline source dumps.

## How It Works

### Retrieval Pipeline

1. **Build file list** -- Indexes all files in the source directory (up to 2000).
2. **LLM structural scan** -- The `CodeRetriever` runs a single LLM call that reviews
   the full structural index and selects architecturally relevant files. Selected files
   are tagged with origin `"selected"`.
3. **Import expansion** -- Python BFS in both directions (depth 2) from selected files.
   Tagged `"import"`.
4. **Hub/facade expansion** -- Highly-connected modules (many importers or importees)
   are pulled in. Tagged `"hub"`.
5. **Neighbor expansion** -- Sibling files from the same directories as selected files.
   Tagged `"neighbor"`.

### Async-to-Sync Bridge

fitz-forge's LLM client is async (`client.generate()` returns a coroutine). fitz-sage's
`CodeRetriever` is synchronous. The `_make_chat_factory` function creates a thin wrapper
that schedules `client.generate()` on the running event loop via
`asyncio.run_coroutine_threadsafe` and blocks for the result. The factory maps tier
names (`fast`, `balanced`, `smart`) to the corresponding model attributes on the client.

### Planning-Specific Post-Processing

After retrieval returns `ReadResult` objects, the gatherer applies several layers:

- **Planning compression** (`compressor.py`) -- AST-based Python compression that
  collapses function bodies to `...` (keeping short bodies under 6 lines), strips
  docstrings and comments, and preserves imports, signatures, and data structures.
  Achieves 50-70% token reduction. Non-Python files get comment stripping and test
  body collapse.
- **Interface signature extraction** (`indexer.py`) -- Uses Python's `ast` module to
  extract class/function signatures with type annotations from selected files. These
  are machine-truth (AST-extracted, not LLM-summarized).
- **Library signature extraction** -- Extracts API signatures from installed packages
  referenced by the selected files. Cross-referenced against all indexed file paths
  to identify which libraries are actually used.
- **Structural index** -- Per-file structural overview built by fitz-sage's
  `build_structural_index`: classes with bases, functions with signatures, imports,
  top-level assignments.

### Context Delivery

A/B testing showed that inline seed source is noise the model ignores, while a one-liner
manifest with on-demand tools saves approximately 4K tokens with zero quality regression.

- **`raw_summaries`** -- Interface signatures + library signatures + one-liner file
  manifest (path + docstring per file). This is what reasoning passes receive.
- **`file_contents`** -- Compressed source for all retrieved files, served on-demand
  via `inspect_files` and `read_file` tools during reasoning.
- **`file_index_entries`** -- Per-file structural detail parsed from the structural
  index, served on-demand via the same tools.
- **`synthesized`** -- Full structural overview (interface sigs + library sigs +
  structural index for all selected files). Used by the implementation check.
- **`full_structural_index`** -- Index covering all 2000 indexed files (not just
  selected ones). Used downstream by the artifact duplicate checker.

### Provenance Tracking

Every included file carries provenance metadata recording its origin signals
(`scan`, `import`, `hub`, `neighbor`) and whether it appeared in the prompt seed set.
The `agent_files` dict in the return value includes `total_screened`, `scan_hits`,
`selected`, `included`, and `file_provenance` for diagnostics.

### Override Mode

For benchmarks, `override_files` accepts a fixed file list and skips all LLM retrieval.
All post-processing (compression, structural overview, provenance) runs identically,
isolating the planning reasoning from retrieval variance.

## Key Design Decisions

1. **Delegate core retrieval to fitz-sage.** One retrieval engine maintained in a
   separate library. fitz-forge only adds planning-specific post-processing.
2. **AST-extracted signatures over LLM summaries.** Interface and library signatures
   are machine truth. The LLM cannot hallucinate function names or parameter types.
3. **Manifest + tools over inline source.** One-liner manifest is approximately 4K
   tokens. Full inline source for 50 files would be 30K+ tokens of noise.
4. **Compression after retrieval, before reasoning.** BM25 and cross-encoder rerankers
   operate on full source for accurate relevance scoring; only the planning LLM sees
   compressed output.
5. **Full structural index for duplicate checking.** The selected-files index covers
   30-50 files. The full index covers the entire codebase so the artifact duplicate
   checker can find existing code that the retrieval did not select.

## Configuration

| Setting          | Default | Description                                    |
|------------------|---------|------------------------------------------------|
| `enabled`        | `true`  | Enable/disable agent context gathering         |
| `max_seed_files` | `50`    | Maximum files to include in retrieval results  |
| `max_file_bytes` | varies  | Maximum bytes to read per file                 |

Configuration is read from `AgentConfig` in the project's config schema.

## Files

| File                                        | Role                                          |
|---------------------------------------------|-----------------------------------------------|
| `fitz_forge/planning/agent/gatherer.py`     | Main gatherer class and async bridge          |
| `fitz_forge/planning/agent/compressor.py`   | AST-based Python compression                  |
| `fitz_forge/planning/agent/indexer.py`      | Interface and library signature extraction    |
| `fitz_forge/config/schema.py`              | `AgentConfig` with retrieval settings         |

## Related Features

- [Implementation Check](02_implementation-check.md) — consumes the `synthesized`
  output to detect already-built features before planning begins.
- [Call Graph Extraction](03_call-graph-extraction.md) — consumes `file_index_entries`
  and the import graph built from the indexed files.
- [Decision Decomposition](04_decision-decomposition.md) — first planning stage,
  receives the one-line file manifest and structural overview.
- [Synthesis](06_synthesis.md) — uses `file_contents` and `file_index_entries`
  for per-artifact generation and `full_structural_index` for the duplicate check.
