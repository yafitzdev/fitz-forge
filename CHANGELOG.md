# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.6.2] - 2026-04-16

### 🎉 Highlights

**Closure catches fabricated classes in every type position** — existence check now fires on parameter/return/variable annotations, `raise`, `except`, `isinstance`, `cast`, and instantiation — not just `ClassName(...)` calls.

**Target class's real method list injected into artifact prompts** — surgical and new-code strategies stop the retry loop where the model invents plausible-sounding helper names like `self._execute_pipeline`.

**Evidence-source artifact injection** — when synthesis produces an empty `needed_artifacts`, files cited as evidence in resolved decisions are auto-injected so plans still produce real artifacts.

### 🚀 Added

- **Evidence-source injection** (`synthesis.py:_enforce_decision_coverage`) — second injection criterion at min_refs=1 for files that are direct evidence sources in a resolved decision.
- **Protocol widening** (`closure.py:_owner_is_protocol`, `_method_exists_anywhere`) — accepts method calls on `Protocol`-typed receivers when the method exists anywhere in the codebase.
- **Enum standard attrs** (`closure.py:_ENUM_STANDARD_ATTRS`, `_is_enum_class`) — `Enum`/`IntEnum`/`StrEnum`/`Flag` subclasses accept `.value`, `.name`, `_value_`, `_name_` automatically.
- **TypeVar detection** (`closure.py:_find_module_typevars`) — skips `T = TypeVar("T")`, `P = ParamSpec("P")`, `Ts = TypeVarTuple("Ts")` bindings plus single-letter uppercase names.
- **Target class self-methods prompt block** (`context.py:_extract_target_self_methods`, `strategy.py:_surgical_grounding_block`) — real method list of the target class is injected into both surgical and new-code prompts.
- **Import-split parse recovery** (`inference.py:try_parse`) — 4th recovery step for outputs that mix top-level imports (indent 0) with indented method bodies.
- **Exact-duplicate closure violation dedup** (`closure.py:_dedupe_exact`) — prevents the same `(artifact, kind, ref)` from firing twice when an annotation walk and a field cascade both flag it.
- **Data-model class validation** (`validate.py:_is_data_class`) — `_check_empty` accepts files with Pydantic `BaseModel`, `dataclass`, `Enum`, `TypedDict`, or any class with annotated fields. Schema/DTO files no longer fail as "empty".
- **Language-aware validation dispatch** (`validate.py:_is_python_file`) — Python AST-based checks skip for `.ts`/`.js`/`.go`/`.rs`/`.java`/`.prisma` files (no alternative structural validation yet — those artifacts pass through unchecked).
- **Cross-language `_check_empty` keywords** — broadened from `def`/`class` to include `function`, `async`, `export`, `const`, `let`, `var`, `model`, `interface`, `enum`, `struct`, `fn`, `pub`. This is the only validation check that currently works across languages.

### 🔄 Changed

- **Generalized class fabrication check** (`closure.py:_iter_annotation_class_names`, `_emit_annotation_types`) — existence check now fires in every type position, not just `ClassName(...)` instantiation.
- **Container-type annotation** (`inference.py:extract_type_name`, `_CONTAINER_TYPES`) — `list[X]`, `dict[K,V]`, `set[X]`, etc. return the container name (skipped via `_SKIP_NAMES`), preventing `items.append()` from being flagged as `Foo.append()`.
- **`_strip_fences` preserves leading indentation** (`strategy.py`) — no longer calls `.strip()` on raw output. Strips blank lines and fences only. Fixes multi-method surgical outputs with mixed indent levels.
- **`_RAW_CODE_INSTRUCTION`** (`strategy.py`) — no longer says "Python code". Now reads "code (full implementation, not just signatures or stubs)".
- **NewCodeStrategy prompt** (`strategy.py`) — explicitly requests "FULL method/function body with implementation logic, not just the signature or type declaration".
- **Grounding uses full-codebase index** (`grounding/check.py:check_all_artifacts`, `grounding/llm.py:validate_grounding`, `orchestrator.py`) — `source_dir` threaded through so `augment_from_source_dir` runs, eliminating false-positive "missing class" on classes outside the retrieval subset.
- **Grounding parser uses `try_parse`** (`grounding/check.py`) — with class-wrap + import-split fallback instead of raw `ast.parse`. Surgical artifacts no longer silently skipped.

### 🐛 Fixed

- **Grounding arity check false positive** (`grounding/index.py`, `grounding/check.py`) — `augment_from_source_dir` now indexes all parameter kinds (positional + keyword-only + `*args` + `**kwargs`), and skips the arity check entirely for variadic callees. Previously triggered `wrong_arity` on any function with keyword-only params.
- **Iterator kind propagation through variable bindings** (`closure.py:_iter_kinds_stack`, `_propagate_var_usage`) — `stream = service.query_stream(...); async for x in stream` now flags as a usage violation when `query_stream` returns a sync iterator. Previously only fired on direct-call usage.
- **Fabricated-owner field cascades** (`closure.py:_dedupe_fabricated_owner_cascades`) — when a parameter type annotation is a fabricated class, every `param.field` access previously fired an independent missing violation. Now collapsed into one root "missing class" violation per `(artifact, owner)`.
- **Regen prompts show real signatures** (`generator.py:_build_repair_hint_block`) — Strategy 2 retries now include sibling method signatures and target class fields, not just error messages.
- **Synthesis `needed_artifacts` empty → 0-artifact plans** — evidence-source injection now fires before the template fallback. Empty `needed_artifacts` plans still produce real artifacts from resolved decisions.

---

## [0.6.1] - 2026-04-13

### 🎉 Highlights

**Artifact Closure Principle** — Five set-level invariants on generated artifacts (Existence, Usage, Kwargs, Imports, Fields) with two repair strategies (expand the set or regenerate the violator). Cross-file inconsistency is a property of the set, not any individual artifact — per-artifact validation can never catch it alone.

**Artifact Black Box** — `generate_artifact(filename, purpose, ctx, ...)` produces one validated artifact; `generate_artifact_set(specs, ctx, ...)` produces a closed artifact set. Pluggable strategies: `SurgicalRewriteStrategy` when a reference method exists, `NewCodeStrategy` otherwise.

**Raw Code Output** — Model outputs Python directly, no JSON wrapping. Eliminated the entire class of quote-mangling bugs from JSON extraction of embedded code. Artifact success rate to 100%.

**Grounding Package Split** — Monolithic `grounding.py` split into `inference.py` (codebase knowledge — return types, fields, MRO), `index.py` (`StructuralIndexLookup` + `augment_from_source_dir`), `check.py` (per-artifact AST check), `llm.py` (LLM gap detection + repair). One home per concern.

### 🚀 Added

- `fitz_forge/planning/artifact/` package — `context.py` (input assembly), `strategy.py` (pluggable strategies), `validate.py` (parseable, fabrication, yield, return-type checks), `closure.py` (five invariants + repair), `generator.py` (entry points).
- `SurgicalRewriteStrategy` + `NewCodeStrategy` with retry loop (up to 3 attempts, each retry includes specific error messages from validation).
- `ArtifactSetResult` with `closed=True/False` and remaining violations.
- Repair strategies: Strategy 1 (expand — add sibling artifact for missing symbol), Strategy 2 (regenerate violator with sibling signature feedback).
- Type tracking for closure checks: function param annotations, `var = ClassName(...)`, service locator return types, and `self._attr` types parsed from the target class's `__init__` in disk source.
- Dedent fallback for fabrication check — surgical artifacts with mixed indent now parse via `textwrap.dedent`.

### 🔄 Changed

- `grounding.py` (monolithic) → `grounding/` package: `inference.py`, `index.py`, `check.py`, `llm.py` with re-exports in `__init__.py` for backwards compatibility.
- Artifact generation no longer asks the model to emit JSON wrapping the code — the filename and purpose are already known, so the model outputs raw code directly.

### 🐛 Fixed

- JSON repair: triple-quoted docstrings in code artifacts (`extract_json` handles them correctly now).
- Subprocess patching scoped so `platform.system()` still works on Windows.
- `tomli` fallback on Python 3.10 for TOML config parsing.
- OpenAI test imports skipped when the `openai` package is absent.
- Orchestrator imports moved to top of file (E402).
- Fixed sleep replaced with terminal-state polling in `test_worker_fifo_order` and other worker tests — faster, deterministic.

### 📊 Stats

- 938 tests

---

## [0.6.0] - 2026-04-09

### 🎉 Highlights

**V2 Deterministic Scorer** — Replaced Sonnet-as-Judge (V1) with a zero-cost deterministic scorer. Completeness (0-30) from taxonomy, artifact quality (0-50) via AST + regex fabrication detection, consistency (0-20) via cross-artifact method/type agreement. Same plan always gets the same score. Source-augmented structural index validates against the full codebase.

**LLM Call Quality Layer** — Single `generate()` function (`fitz_forge/llm/generate.py`) wraps all 36 LLM call sites. Context-aware max_tokens capping (budget = context_size - prompt_tokens - 512), output sanitization, truncation detection + retry. Fabrications down 43% vs baseline.

**Full LLM Provenance + Stage Replay** — Every `generate()` call writes a JSON trace (messages, output, timing). Stage snapshots saved after each pipeline stage. New `replay` command loads a snapshot and re-runs only the remaining stages — test pipeline changes without re-running the full 10-minute pipeline.

**Scorer Accuracy: avg 90.0/100** — Three scorer fixes eliminated false consistency failures: parse recovery in method extraction, codebase method awareness (skip calls to existing methods), private method exclusion. First perfect 100/100 plan. 4/10 plans score 95+.

### 🚀 Added

- V2 deterministic scorer: `benchmarks/eval_v2_deterministic.py` with completeness, artifact quality, consistency checks
- V2 taxonomy framework: `benchmarks/streaming_taxonomy.json` for task-specific file requirements
- `fitz_forge/llm/generate.py` — standalone generate function with budget cap, sanitization, truncation retry, provenance tracing
- `configure_tracing(trace_dir)` / `get_trace_dir()` for opt-in JSON provenance per generate() call
- Stage snapshots: `snapshot_after_{stage_name}.json` saved to trace dir after each pipeline stage
- `replay` command in plan_factory: load snapshot, skip completed stages, re-run rest with real LLM
- `_SnapshotCheckpointManager` for feeding saved state to orchestrator resume logic
- Labeled LLM calls: `decomp_candidate_N`, `resolve_dN`, `synthesis_reasoning_N`, `artifact_surgical_*`, `artifact_*`
- Decomp scorer: `ref_complete` criterion (15pts) — penalizes missing definition files in decisions
- Per-criterion quality gates: each decomposition criterion must clear its minimum, retry up to 4 candidates
- Consistency cascade fix: unparseable artifacts excluded as targets
- Source-dir augmentation: `augment_from_source_dir()` scans full codebase, merges methods into index classes
- Size-weighted artifact quality: larger artifacts carry more weight in the mean
- Regex fabrication fallback: detect fabrications on unparseable code via string scan
- Artifact dedup: removes duplicate filenames post-generation, keeps longest content
- 8 failure pattern docs (`docs/v2-scoring/V2-F1` through `V2-F8`) with 5-why analysis
- Benchmark tracker: `docs/v2-scoring/TRACKER.md` with run history, scoring formula, changelogs
- Per-artifact generation with deterministic scoring (`3b8ef270`)
- Class interface injection + deterministic repair for artifacts (`3acf4940`)
- Type-aware deterministic repair — AST-based init attr extraction prevents false positives (`7e079300`)
- Best-of-3 synthesis reasoning with scope consensus (`1bf4470f`)
- Imported type API injection + stronger artifact rules (`9ecee911`)
- F9 reference method injection — stubs replaced with real implementations (`a1418df4`)
- F12 artifact filename cleanup — strip method suffixes + reject invalid names (`fbee7208`)
- F13C fallback — derive approach from key_tradeoffs when empty (`d9d345e0`)
- 14 documented failure patterns (F1–F14) with benchmarks and fixes (`6e2fcdd6`)
- Ruff + mypy configuration with CI linting step
- CONTRIBUTING.md with architecture guidelines and PR process
- PR template with code quality checklist
- Progressive examples directory (quickstart, config, MCP integration)
- `tools/ci_check.py` — local pre-push verification (format + lint + optional tier1 tests)
- `tools/pre_release.py` — comprehensive pre-release validation (format, lint, imports, tests, build)
- Test tier markers (tier1–4) for selective CI stages
- `docs/ARCHITECTURE.md` — standalone architecture reference with layer diagram and data flow
- `docs/features/` — 17 detailed feature docs covering both pipelines and infrastructure
- `docs/CONFIG.md` — complete configuration reference with every field explained
- `docs/TROUBLESHOOTING.md` — GPU issues, Windows quirks, pipeline debugging
- 4 decomposed pipeline docs: call graph extraction, decision decomposition, decision resolution, synthesis

### 🔄 Changed

- All 36 `client.generate()` call sites migrated to standalone `generate()` function
- Reference method detection broadened: matches any method name in purpose text against source file (no verb pattern required, includes private methods)
- Consistency checker uses structural index to skip calls to existing codebase methods
- Private method calls (`_foo`) excluded from consistency checks
- `_extract_method_definitions` uses parse recovery (dedent/class wrap) matching artifact checker
- Compact synthesis prompts with budget-aware reasoning truncation (`8804d1e3`)
- Sectioned extraction — roadmap/risk stages consume design output (`58aa3591`)
- Reasoning compression for artifact prompts replaces hard truncation (`bbc6cf9a`)
- Renamed fitz-graveyard → fitz-forge (package, CLI, config, all references) (`91b026f9`)

### 🐛 Fixed

- Scorer: false consistency failures from unparseable surgical artifacts (V2-F6a)
- Scorer: false consistency failures from calls to existing codebase methods (V2-F6b)
- Scorer: false consistency failures from private method calls
- Pipeline: fabrication on tangential files (instrumentation.py) due to narrow reference method regex
- Reorder artifact prompt — rules+grounding FIRST, reasoning last (`91856485`)
- Remove artificial caps, add budget-aware reasoning truncation (`9c23a62b`)
- Skip known init attrs in type-aware repair to prevent false positives (`b538c970`)
- Read uncompressed disk source for interface + type-map extraction (`68cc4219`)
- Remove hardcoded field patterns + guard ambiguous import repair (`4fa7f067`)
- Extend F12 to strip `::` method suffix (`15d726e6`)

### 📊 Stats

- **V2 Scorer: avg 90.0/100** (range 77.1-100.0), 4/10 plans at 95+, first 100/100 plan
- Run progression: 77.6 → 86.5 → 88.3 → 84.6 → **90.0** (runs 81-89)
- Fabrications: 18 → 1 → 14 → 14 → **8** (runs 82-89)
- Completeness: 30/30 on all plans since run 88
- V1 Scorer (legacy): 45.3/60, best individual 53/60
- 960+ tests

---

## [0.5.0] - 2026-03-29

### 🎉 Highlights

**Decomposed Planning Pipeline** — Replaced the monolithic 3-stage pipeline with a decision-based architecture. The LLM decomposes the task into atomic decisions, resolves each with codebase evidence, then synthesizes a coherent plan. Scored 43.4/60 avg on Sonnet-as-Judge evaluation (up from 20/60 with the original pipeline).

**Tool-Enriched Template Extraction** — Codebase lookup tools (lookup_method, lookup_class, read_method_source) gather verified class/method signatures during artifact generation. Tool results are injected into the template extraction context, producing grounded artifacts with correct method signatures. Best plans scored 46-48/60.

**Sonnet-as-Judge Evaluation System** — 6-dimension scoring rubric (file identification, contract preservation, internal consistency, codebase alignment, implementability, scope calibration) evaluated by Claude Code subagents. 30 benchmark runs tracked with full score breakdowns. No Anthropic SDK needed.

**llama-cpp Provider** — Native llama-server subprocess management with flash attention, q8_0 KV cache, WDDM GPU degradation detection, and tok/s baseline tracking. Replaces LM Studio for inference when configured.

### 🚀 Added

- Decomposed planning pipeline: decision decomposition → resolution with evidence → synthesis (`de51dbee`)
- Per-decision resolution with full-signature codebase evidence (`53c4fe46`)
- Tool-assisted artifact building: lookup_method, lookup_class, read_method_source (`37e70d4f`)
- Tool-enriched template: tool results → "VERIFIED CODEBASE INFO" context for template extraction (`4ed3b16d`)
- `_strip_module()` in codebase tools — handles fully-qualified names like `fitz_sage.sdk.fitz.Fitz` → `Fitz` (`e17c64e9`)
- Pydantic field extraction in lookup_class — returns annotated fields for BaseModel subclasses (`707b13e8`)
- Normalized dedup cache keys — module-path variants caught as duplicates (`4ed3b16d`)
- Early stale exit — 2 consecutive all-duplicate tool rounds → template fallback (`4ed3b16d`)
- Post-synthesis grounding validator: AST path checks fabricated methods, LLM path checks architectural gaps (`5711b23a`)
- Template-constrained cheat sheet: auto-extracts instance attrs from `__init__` via AST (`af11d1e2`)
- Sonnet-as-Judge plan evaluation with 6-dimension rubric (`5711b23a`)
- `tool_choice` parameter on generate_with_tools for both llama_cpp and lm_studio clients (`4ed3b16d`)
- llama-cpp provider with subprocess management, health checks, VRAM monitoring
- GPU degradation detection + tok/s baseline tracking for consumer Blackwell cards
- `inspect_files(paths)` tool in reasoning pipeline (`8dd1dfde`)
- File manifest context delivery replaces inline seed source (`8dd1dfde`)
- 40-query retrieval quality benchmark with ground truth (`12c9de50`)

### 🔄 Changed

- check_exists removed from tool list — model over-used it (15+ calls), causing degeneration (`e17c64e9`)
- `_build_artifacts_with_tools` returns `(artifacts, tool_context)` tuple instead of just artifacts (`4ed3b16d`)
- Template extraction receives cheat sheet + tool-verified signatures combined (`4ed3b16d`)
- Default `max_seed_files` bumped from 30 to 50 (`8dd1dfde`)
- Resolution prompt demands complete param lists, parallel methods must match originals (`53c4fe46`)

### 🐛 Fixed

- `health_check` no longer force-switches models (`e904b587`)
- Clean error messages instead of full tracebacks (`664e48dc`)

### 📊 Stats

- 30 benchmark runs, 150+ scored plans
- Best config: 43.4/60 avg (run 28), individual plans up to 48/60
- Score progression: 20 → 35.6 → 37.8 → 39.2 → 41.4 → 43.4

---

## [0.4.1] - 2026-03-24

### 🎉 Highlights

**Single Model Pipeline** — The hybrid 4B/30B model split is gone. Qwen3-Coder-30B (MoE, 3B active) handles both retrieval and reasoning — benchmarked at 89% critical recall across 40 queries, actually faster than the 4B (18s vs 22s per query). No model switching, no VRAM churn, no CUDA context destruction.

**Manifest + inspect_files Tool** — A/B tested 3 context delivery approaches (10 runs each, temp=0). Inline seed source was noise the model ignored (5K tokens wasted). Full structural index in the prompt was load-bearing but expensive. Solution: one-liner file manifest in prompt (~4K tokens) + `inspect_files(paths)` tool for on-demand structural detail. 40% faster reasoning, 10/10 consistency, zero quality regression. 50+ files now fit in 32K context with headroom for tool use.

**Retrieval Quality Benchmark** — 40-query ground truth eval for code retrieval with critical/relevant file scoring, per-category breakdown, and most-missed file tracking. Used to systematically evaluate all optimization candidates.

### 🚀 Added

- `inspect_files(paths)` tool in reasoning pipeline — returns classes, methods, imports for requested files on demand (`8dd1dfde`)
- File manifest context delivery: one-liner (path + docstring) replaces full structural index in `raw_summaries` (`8dd1dfde`)
- `file_index_entries` dict in agent output for `inspect_files` tool serving (`8dd1dfde`)
- 40-query retrieval quality benchmark with ground truth (`12c9de50`)

### 🔄 Changed

- Default `max_seed_files` bumped from 30 to 50 — manifest approach makes more files cheap (`8dd1dfde`)
- Reasoning prompts no longer include inline seed file source (~5K tokens saved per call) (`8dd1dfde`)
- Tool hint updated: inspect-first workflow (inspect_files → read_file) replaces seed-set exploration (`8dd1dfde`)
- Health check retries on model load failure instead of crashing (`e904b587`)
- Error messages show clean text instead of full tracebacks (`664e48dc`)

### 🗑️ Removed

- Hybrid model pipeline (separate 4B retrieval + 30B reasoning) — single model handles both (`8dd1dfde`)
- Inline seed file source in `raw_summaries` — replaced by manifest + inspect_files tool (`8dd1dfde`)

### 🐛 Fixed

- `health_check` no longer force-switches models when checking provider availability (`e904b587`)

---

## [0.4.0] - 2026-03-15

### 🎉 Highlights

**fitz-sage Powered Retrieval** — Code retrieval now delegates to fitz-sage's `CodeRetriever`, replacing the internal retrieval implementation. Single maintained retrieval mechanism across both projects.

**Hybrid Model Pipeline** — Qwen3.5-4B for code retrieval, Qwen3-Coder-30B for planning. The orchestrator auto-switches between models via LM Studio CLI (`lms load`/`lms unload`). Smart model switching checks what's already loaded to avoid CUDA context destruction on consumer GPUs.

**Hub + Facade Retrieval Signals** — Two new deterministic signals that don't depend on LLM judgment. Hub files (>5 forward imports) are auto-included as architectural orchestrators. Facade expansion follows `__init__.py` re-exports to reach actual definitions. Combined with a relative import resolution fix, `engine.py` and `answer.py` discovery went from 0% to 100% across 10 benchmark runs.

**Benchmark Factory** — Rapid A/B testing of pipeline changes. Retrieval benchmarks (~12s/run) and reasoning benchmarks with fixed file lists via `override_files`. Used to systematically evaluate 4 optimization candidates across 25 runs.

**Devil's Advocate Removal** — Benchmarked across 5 runs: removing the devil's advocate pass improved architecture quality from 60% to 100% correct decisions. The pass was over-correcting, pushing the model toward protocol-breaking "cleaner" solutions.

**Split Reasoning** — Arch+design and roadmap+risk stages can each split into two sequential LLM calls (architecture then design, roadmap then risk). Reduces peak context from ~29K to ~8K tokens per call, enabling dense 27B models at 32K context. Auto-enabled when `context_length < 32768`. Benchmarked at 5/5 correct architecture decisions with 5 seed files.

**Artifact Duplicate Check** — Before the arch+design stage, proposed new files are searched against the full codebase structural index (all files, not just selected ones). When the model proposes `cache.py`, the checker finds `cloud/client.py: invalidate_cache(reason, scope)` and warns the architecture stage to extend existing code instead of building from scratch. Pure Python — no LLM call.

### 🚀 Added

- Artifact duplicate check: searches full structural index for existing files matching proposed deliverables (`f80a965a`)
- Full structural index stored in agent output for downstream duplicate checking (`162dcd8b`)
- Hub import expansion: hub files' imports are now followed to catch orchestrated subsystems (`fitz-sage 6085578`)
- Post-limit facade swap: `__init__.py` files replaced with actual implementations in final selection (`fitz-sage 6085578`)
- Split reasoning mode for arch+design stage: `ArchitectureDesignStage(split_reasoning=True)` (`ae7ecaa7`)
- Split reasoning mode for roadmap+risk stage: `RoadmapRiskStage(split_reasoning=True)` (`f50dfec3`)
- `create_stages(split_reasoning=True)` factory function for both splits (`f50dfec3`)
- Auto-split detection in worker: enabled when `context_length < 32768` (`50526cf5`)
- Smart model context override: 4B agent loads with 65K context regardless of config (`7014f2b1`)
- `--split` and `--max-seeds` flags on reasoning benchmark (`d9241eb4`)
- LM Studio model tier support: `fast_model`, `smart_model` config fields (`ba61ffe9`)
- Auto model switching in orchestrator between agent (Qwen3.5-4B) and planning (Qwen3-Coder-30B) stages (`ba61ffe9`)
- `switch_model()` on LMStudioClient with loaded-model check (`b3b6a9c1`)
- `get_loaded_model()` parses `lms ps` output for specific model identification (`b3b6a9c1`)
- Hub file auto-inclusion in retrieval: files with >5 forward imports always selected (`dcf1f1c0`)
- Hub hint in LLM scan prompt for architectural awareness (`dcf1f1c0`)
- Facade expansion: `__init__.py` re-exports followed to actual definitions (`dcf1f1c0`)
- `"hub"` and `"facade"` origin signals in file provenance tracking (`dcf1f1c0`)
- Benchmark factory: `python -m benchmarks.plan_factory retrieval/reasoning` (`dcf1f1c0`)
- `override_files` param on `AgentContextGatherer.gather()` for fixed-retrieval benchmarks (`335dda72`)
- `_bench_override_files` param on orchestrator for benchmark integration (`335dda72`)
- 5 post-reasoning verification sub-agents in arch+design stage (`45bd0f70`)
- Type boundary audit agent (`4a275ebe`)
- Plan diagnostics section with stage timings and file provenance (`5293e03e`)

### 🔄 Changed

- Minimum context window lowered from 32K to 8K tokens — split reasoning enables small-context models (`50526cf5`)
- Investigations use `gathered_context` (32K cap) instead of `raw_summaries` (100K+) — 70% input reduction per call (`23ca676a`)
- Health check loads `smart_model` first when configured, avoiding redundant model switches (`c7ba836e`)
- Critique length threshold uses absolute floor (2000 chars) for focused critiques (`ba61ffe9`)
- Replaced internal retrieval with fitz-sage `CodeRetriever` — single maintained retrieval mechanism (`a47f11b3`)

### 🗑️ Removed

- Devil's advocate pass from arch+design stage — benchmarked as harmful to quality (`304f8f6c`)

### 🐛 Fixed

- Relative imports (`from .X import Y`) now resolved in import graph — previously silently dropped (`fitz-sage 0e7ed8b`)
- `switch_model` no longer unloads a model that's already the target (`b3b6a9c1`)
- Health check no longer loads the wrong model first when hybrid setup is configured (`c7ba836e`)

---

## [0.3.0] - 2026-03-11

### 🎉 Highlights

**Structural Scan Only** — Stripped BM25, embedding, and cross-encoder reranking from the retrieval pipeline. The LLM structural index scan alone finds all architecturally important files. Agent gathering dropped from ~155s to ~30s. Removed 867 lines of retrieval complexity, the `sentence-transformers` runtime dependency, and the VRAM unload/reload dance.

**Seed-and-Fetch** — Only 30 high-priority files go into the planning prompt as seeds. Remaining files are available via `read_file`/`read_files` tools during reasoning. Forces the LLM to actively explore the codebase rather than passively consuming a 150-file context dump.

**Enriched Structural Index** — The AST-extracted index now includes module docstrings, return type annotations, and key decorators (`@dataclass`, `@abstractmethod`, etc.). Gives the LLM semantic, type-flow, and architectural cues that improved architectural recommendations from wrong to roughly correct.

**llama.cpp Provider** — New provider that manages a `llama-server` subprocess directly. Single model path across all tiers prevents CUDA context destruction on consumer GPUs (WDDM degradation bug). Flash attention, KV cache type, and GPU layer offloading are all configurable.

### 🚀 Added

- Seed-and-fetch context architecture: 30 seed files in prompt, rest via tool calls (`48853d8`)
- `read_file(path)` and `read_files(paths)` tools for LLM reasoning stages (`48853d8`)
- Disk fallback for tool reads: files not in pool read from source dir on demand (`48853d8`)
- `max_seed_files` config option (default 30) (`48853d8`)
- Module docstrings in structural index as `doc: "..."` (`fd9fbe7`)
- Return type annotations on functions/methods: `chat() -> str` (`fd9fbe7`)
- Key decorator display: `[@dataclass]`, `[@abstractmethod]` (`fd9fbe7`)
- llama.cpp provider with llama-server subprocess management (`bc4fe4a`)
- WDDM degradation fix: same model path across tiers prevents CUDA context churn (`47d797a`)
- GPU temperature guard: preflight cooldown + mid-stream throttle (`e9bfc3d`)
- Tok/s baseline tracking with degradation warnings (`47d797a`)
- AST-based code compression for planning context (77% reduction) (`59e1246`)
- Adaptive context delivery: investigation findings routed into reasoning prompt (`f1359bd`)
- VRAM-aware model loading + eject after pipeline (`17f6835`)
- Per-file provenance tracking: signals (scan, import, neighbor) and role (seed, tool_pool) (`52280ae`)
- Decomposed reasoning with parallel investigation calls (`9c61e90`)
- Interface signature cheat sheet and devil's advocate pass (`15d1662`)
- Pipeline diagnostics: provider, model, timings, call counts (`c6df329`)
- `max_tokens=16384` default on all generate methods — prevents infinite generation (`95c59ee`)
- `enable_thinking: false` for Qwen3 models (`c6df329`)

### 🔄 Changed

- Retrieval pipeline: map → expand → scan → import → neighbor → read (was 9 passes with BM25/embed/rerank) (`1113614`)
- Structural scan is now the sole file selection signal (`1113614`)
- Provenance signals reduced to scan/import/neighbor (removed bm25/embed/rerank) (`1113614`)
- Import expansion: forward-only depth 1, from scan hits only (`93d4633`)
- Neighbor expansion: only import-reachable directories expand (`c93c32b`)
- Neighbors inserted adjacent to trigger file, not appended (`31a89c5`)

### 🗑️ Removed

- BM25 keyword screening (`1113614`)
- Embedding recall via sentence-transformers (`1113614`)
- Cross-encoder reranking (`1113614`)
- VRAM router + LLM unload/reload during retrieval (`1113614`)
- `EmbeddingModel` and `RerankerModel` classes (`1113614`)
- `embedding_model` and `reranker_model` config options (`1113614`)
- `max_summary_files` cap — replaced by seed-and-fetch (`054ae35`)

### 🔧 Fixed

- OOM protection: skip embedding/reranking when LLM unload fails (`1b87fd1`)
- WMI deadlock on Windows with pytest + lazy ollama imports (`8acdb33`)
- Infinite generation from llama-server context-shift loops (`95c59ee`)
- WDDM GPU performance degradation on Blackwell consumer cards (`47d797a`)
- Mixed KV cache types (K=f16, V=q8_0) break flash attention — documented workaround

### 📊 Stats

- 646 tests

---

## [0.2.0] - 2026-03-01

### 🎉 Highlights

**Structural Index Agent** — Replaced LLM-based file selection with a Python AST structural index. The agent extracts classes, functions, and imports from source files, then navigates by keyword matching to pick task-relevant files. More accurate, faster, and no longer confused by noise directories like `.hypothesis/`. New pipeline: map → index → navigate → summarize → synthesize.

**Implementation Check** — A surgical LLM call after agent context gathering asks one question: "is this task already implemented?" The result is injected as ground truth into all downstream pipeline stages. Prevents plans from proposing to build code that already exists.

**Section-Specific Confidence Scoring** — Rewrote the confidence scorer from a coarse 1-5 scale (0.2 steps) to a 1-10 scale (0.1 steps). Each section type (context, architecture, design, roadmap, risk) has its own scoring criteria, including correctness checks like "does it acknowledge existing implementations."

### 🚀 Added

- Structural index builder extracting classes, functions, imports from Python files (`7d22bb7`)
- Keyword-aware navigation prompt replacing LLM file selection (`7d22bb7`)
- Implementation check pass with `{"already_implemented", "evidence", "gaps"}` output (`c50b779`)
- `_get_implementation_check()` helper injecting check result into stage prompts (`c50b779`)
- "Already Implemented" section in agent synthesize prompt (`c50b779`)
- Section-specific scoring criteria for context, architecture, design, roadmap, risk (`f7d4291`)
- 1-10 LLM scoring scale with `\b(10|[1-9])\b` extraction (`f7d4291`)

### 🔄 Changed

- Agent pipeline: map → index → navigate → summarize → synthesize (was map → select → summarize → discover → synthesize) (`7d22bb7`)
- Context stage `needed_artifacts` mini-schema now indicates empty list is valid (`c50b779`)
- Confidence scorer hybrid formula unchanged (0.7 LLM + 0.3 heuristic) but with finer granularity (`f7d4291`)

### 🔧 Fixed

- Pipeline stage fixes: roadmap_risk field extraction, risk schema defaults (`c257461`)
- Agent summarize prompt improvements (`c257461`)
- CLI enhancements (`c257461`)

### 📝 Docs

- Rewrote README with motivation, collapsible sections, PyPI install (`18be1ed`)
- Updated CLAUDE.md with new agent pipeline and implementation check (`b466673`)
- Added PyPI badge and link (`e8f0371`)

### 📊 Stats

- 402 tests

---

## [0.1.0] - 2026-02-20

### 🎉 Highlights

**Local-First AI Planning** — Queue a planning job, let it run on local hardware, wake up to a full architectural plan. Two interfaces (CLI + MCP) over the same `tools/` service layer with SQLite job queue.

**Per-Field Extraction Pipeline** — 3 merged planning stages, each using 1 reasoning pass + 1 self-critique + N tiny JSON extractions (<2000 chars). Small enough for a 3B quantized model to produce valid structured output.

### 🚀 Added

- MCP server + Typer CLI dual interface over shared `tools/` service layer (`e833447`)
- SQLite job queue with WAL mode, crash recovery (`running` → `interrupted`)
- 3 merged pipeline stages: Context (4 groups), Architecture+Design (6 groups), Roadmap+Risk (3 groups)
- Agent context gatherer (multi-pass: map → select → summarize → synthesize)
- Ollama provider with OOM fallback (80B → 32B) (`190ac03`)
- LM Studio provider via OpenAI-compatible API (`11d6c93`, `1d33ad9`)
- Cross-stage coherence check
- Confidence scoring + optional Anthropic API review pass
- Clarification questions run after codebase analysis (`7e49d99`)

### 📊 Stats

- 391 tests

[Unreleased]: https://github.com/yafitzdev/fitz-forge/compare/v0.6.2...HEAD
[0.6.2]: https://github.com/yafitzdev/fitz-forge/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/yafitzdev/fitz-forge/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/yafitzdev/fitz-forge/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/yafitzdev/fitz-forge/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/yafitzdev/fitz-forge/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/yafitzdev/fitz-forge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/yafitzdev/fitz-forge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/yafitzdev/fitz-forge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yafitzdev/fitz-forge/releases/tag/v0.1.0
