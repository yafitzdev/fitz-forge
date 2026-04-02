# fitz-forge Planning Pipeline Architecture

Technical reference for the decomposed planning pipeline. This is the default pipeline used by `fitz-forge plan` and the MCP server.

## Pipeline Overview

```
User Request
    |
    v
[Agent Context Gathering]  -----> Codebase retrieval + compression + structural index
    |                              LLM-powered file selection (6-8 calls)
    |                              Output: file_contents, structural_index, synthesized overview
    v
[Implementation Check]  ---------> "Is this already implemented?" (1 LLM call)
    |
    v
[Call Graph Extraction]  ---------> Deterministic AST import/call graph (0 LLM calls)
    |
    v
[Decision Decomposition]  -------> Break task into atomic decisions (1-2 LLM calls)
    |                               Output: 10-15 decisions with evidence requirements
    v
[Decision Resolution]  ----------> Resolve each decision against source code (1 LLM call per decision)
    |                               Output: decided + reasoning + evidence + constraints
    v
[Synthesis]  ---------------------> Narrate decisions into plan (1 reasoning + 1 critique + 13 extractions)
    |   |                           Per-field extraction: context(4) + arch(2) + design(3) + roadmap(1) + risk(2)
    |   |
    |   +--[Per-Artifact Generation]  --> 1 generate() call per artifact file
    |       |                             Injected: source code + class interfaces + schema fields
    |       +--[Type-Aware Repair]  ----> Deterministic post-generation fix (0 LLM calls)
    |
    v
[Grounding Validation]  ---------> AST check + LLM architectural review
    |                               Deterministic repair of violations
    v
[Coherence Check]  --------------> Cross-stage consistency (1 LLM call)
    |
    v
[Confidence Scoring]  -----------> Section-specific quality scores (1 LLM call)
    |
    v
[Plan Rendering]  ----------------> Markdown output + file write
```

## Stage Details

### 0. Agent Context Gathering (progress 0.00-0.09)

**File**: `fitz_forge/planning/agent/gatherer.py`

**Purpose**: Retrieve and compress relevant codebase files for the planning pipeline.

**Flow**:
1. Build structural index via AST (classes, methods, functions, imports per file)
2. LLM-powered file retrieval: query expansion + structural scan + BM25 + embedding + cross-encoder rerank
3. Import expansion (BFS depth 2) + neighbor expansion (sibling files)
4. Compress selected files via AST compressor (`compressor.py`):
   - Strip docstrings, comments, blank lines
   - Collapse method bodies to `... # N lines` (keep bodies < 6 lines)
   - **Init preservation**: keep `self._xxx = ClassName(...)` in `__init__`/`_init_components`
5. Build interface signatures + library API reference

**Output**:
```python
{
    "synthesized": str,           # Compact overview (one-liner manifest + sigs)
    "raw_summaries": str,         # Structural overview + seed file contents
    "file_contents": dict,        # path -> compressed source (30 files)
    "full_structural_index": str, # All indexed files' class/method/function entries
    "agent_files": dict,          # Provenance metadata (scan hits, imports, etc.)
}
```

**LLM calls**: 6-8 (embedded in retrieval)
**Checkpointed**: Yes (`_agent_context`)

---

### 0.5. Implementation Check (progress 0.092)

**File**: `fitz_forge/planning/pipeline/orchestrator.py`

**Purpose**: Early signal — is this task already implemented?

**LLM call**: 1 (template: `implementation_check.txt`)
**Output**: `{already_implemented: bool, evidence: str, gaps: [str]}`
**Injected into**: all downstream stage prompts

---

### 0.6. Call Graph Extraction (progress 0.095)

**File**: `fitz_forge/planning/pipeline/call_graph.py`

**Purpose**: Build import/call dependency graph from selected files.

**Deterministic** (no LLM calls):
- AST-extracts imports between selected files
- Builds directed graph of file dependencies
- Identifies entry points, interior layers, leaf nodes
- Serialized as `_call_graph_text` for synthesis prompt

---

### 1. Decision Decomposition (progress 0.10-0.20)

**File**: `fitz_forge/planning/pipeline/stages/decision_decomposition.py`

**Purpose**: Break the user's task into atomic, resolvable decisions.

**Prompt**: Template `decision_decomposition.txt` with:
- Task description
- Gathered context (structural overview)
- Implementation check result
- Call graph

**LLM calls**: 1-2 (reasoning + optional retry if < 5 decisions)

**Output**: 10-15 decisions, each with:
```python
{
    "decision_id": "d1",
    "question": "Does X support Y?",
    "category": "interface_contract|data_flow|error_handling|...",
    "evidence_needed": ["file.py: ClassName.method signature"],
    "depends_on": ["d0"],  # decision ordering
}
```

**Post-processing**:
- Coverage gate: checks if interior call-graph layers have decisions
- If gaps found, retries with explicit layer warning

---

### 2. Decision Resolution (progress 0.20-0.50)

**File**: `fitz_forge/planning/pipeline/stages/decision_resolution.py`

**Purpose**: Resolve each decision by examining actual source code.

**Per-decision flow** (sequential, 1 LLM call each):
1. Build prompt with decision question + evidence requirements
2. Inject relevant source code (from file_contents, read from disk if needed)
3. LLM answers with evidence citations from real code
4. Extract: `{decision_id, decision, reasoning, evidence, constraints_for_downstream}`

**LLM calls**: 1 per decision (10-15 total)

**Output**: List of resolutions with grounded evidence and binding constraints.

---

### 3. Synthesis (progress 0.75-0.95)

**File**: `fitz_forge/planning/pipeline/stages/synthesis.py`

**Purpose**: Narrate pre-solved decisions into the final structured plan.

**The model is NOT discovering anything new** — it's organizing pre-solved answers into a coherent architectural document.

#### 3a. Synthesis Reasoning (1 LLM call)

**Prompt** (compact format, ~44K chars):
- All resolved decisions (decision text + evidence signatures + constraints, NO reasoning)
- Gathered context (structural overview, ~20K chars)
- Call graph
- Template instructions: write Context + Architecture + Design + Roadmap + Risk as prose

**Output**: 15-20K chars of free-form architectural narrative

#### 3b. Self-Critique (1 LLM call)

Checks for: scope inflation, hallucinated APIs, missed existing code, vague hand-waving.

#### 3c. Per-Field Extraction (13 LLM calls)

Each call extracts a small group of fields from the reasoning into a tiny JSON schema.

**Context extraction** (4 groups, receives `krag_context`):
| Group | Fields |
|-------|--------|
| description | project_description, key_requirements, constraints, existing_context |
| stakeholders | stakeholders, scope_boundaries |
| files | existing_files, **needed_artifacts** |
| assumptions | assumptions (with impact + confidence) |

**Architecture extraction** (2 groups, receives `krag_context`):
| Group | Fields |
|-------|--------|
| approaches | approaches, recommended, reasoning, scope_statement |
| tradeoffs | key_tradeoffs, technology_considerations |

**Design extraction** (3 groups, receives `krag_context`):
| Group | Fields |
|-------|--------|
| adrs | adrs (Architectural Decision Records) |
| components | components, data_model |
| integrations | integration_points |

**Roadmap extraction** (1 group, receives **slim Design output + constraints**):
| Group | Fields |
|-------|--------|
| phases | phases (number, name, objective, deliverables, dependencies, verification_command, effort) |

**Risk extraction** (2 groups, receives **slim Design output + constraints**):
| Group | Fields |
|-------|--------|
| scheduling | critical_path, parallel_opportunities, total_phases |
| risks | risks, overall_risk_level, recommended_contingencies |

**Sectioned context**: Roadmap and Risk get slim Design output (components, interfaces, artifact filenames — no code bodies) + decision constraints only. Context/Architecture/Design get full codebase context.

#### 3d. Per-Artifact Generation

**Triggered by**: `needed_artifacts` from context extraction.

For each needed artifact file (capped at 8):

1. **Find source**: disk fallback for uncompressed source (`_source_dir`)
2. **Resolve class interfaces** (deterministic, from uncompressed disk source):
   - Parse `__init__`/`_init_components` for `self._xxx = ClassName(...)`
   - Look up each ClassName's public methods via structural index + source AST
   - Inject cheat sheet: `self._router -> RetrievalRouter: route(query, top_k), ...`
   - Capped at 50 interface lines
3. **Resolve schema fields** (deterministic):
   - Find CamelCase class names in decisions/reasoning
   - Extract Pydantic field names via AST
4. **Build type-attr map** (deterministic):
   - Reverse map: `ClassName -> attr_name` (e.g., `GovernanceDecider -> _governor`)
5. **Extract all init attrs** (deterministic):
   - All `self._xxx` from init methods, regardless of RHS pattern
6. **Compress reasoning**: Keep architecture + design sections, drop roadmap/risk
7. **Generate** (1 LLM call per artifact, max_tokens=4096):
   - Prompt: decisions + reasoning (compressed) + source code + interfaces + schema fields
   - Budget-aware: 32K token limit, reasoning truncated first
8. **Post-generation repair** (deterministic):
   - Type-aware resolution: match fabricated names against CamelCase type parts
   - Test method leak filter: strip `self.test_*()` from non-test files
   - Fuzzy matching: difflib similarity >= 0.82
   - Skip known init attrs (prevent false positives)

---

### 4. Grounding Validation (post-synthesis)

**File**: `fitz_forge/planning/validation/grounding.py`

**Path 1 — AST Check** (deterministic):
- Parse each artifact with `ast.parse()`
- Check `self.method()` calls against `StructuralIndexLookup`
- Check `self._attr` references against known attributes
- Check class/function references against index
- Returns list of `Violation(artifact, line, symbol, kind, detail, suggestion)`

**Path 2 — LLM Repair** (per artifact with violations):
- Prompt: artifact content + violation list + available methods
- LLM produces corrected artifact
- Applied only if corrections parse and reduce violations

---

### 5. Coherence Check (post-grounding)

**File**: `fitz_forge/planning/pipeline/orchestrator.py`

1 LLM call: verifies context -> architecture -> design -> roadmap -> risk are mutually consistent.

---

### 6. Confidence Scoring + Rendering

- Section-specific quality scores (1 LLM call)
- Markdown rendering with all sections
- File write to `~/.fitz-forge/plans/plan_<job_id>.md`

---

## LLM Call Summary

| Stage | Calls | Notes |
|-------|-------|-------|
| Agent context gathering | 6-8 | LLM-powered retrieval |
| Implementation check | 1 | Early exit signal |
| Call graph | 0 | Deterministic AST |
| Decision decomposition | 1-2 | + optional retry |
| Decision resolution | 10-15 | 1 per decision |
| Synthesis reasoning | 1 | Compact prompt (~44K chars) |
| Self-critique | 1 | |
| Field extraction | 13 | 4+2+3+1+2+1(scheduling) |
| Per-artifact generation | 3-5 | 1 per needed file |
| Grounding repair | 0-5 | 1 per violated artifact |
| Coherence check | 1 | |
| Confidence scoring | 1 | |
| **Total** | **~38-52** | **~6-8 min on RTX 5090** |

## Token Budget (32K target)

| Prompt | Chars | Tokens (est) |
|--------|-------|-------------|
| Synthesis reasoning | 44K | ~11K |
| Per-field extraction | 5-8K | ~1.5-2K |
| Per-artifact generation | 10-15K | ~3-4K |
| Roadmap/Risk extraction | 8K | ~2K |

All prompts within 32K budget for codebases up to 2-3x fitz-sage size.

## Key Design Principles

1. **Decompose**: Break hard problems into small, focused LLM calls (per-decision, per-field, per-artifact)
2. **Ground**: Every claim must trace to AST-extracted evidence, not LLM inference
3. **Repair deterministically**: Type-aware repair, fuzzy matching, test leak filter — no LLM in the repair loop
4. **Right-size context**: Each LLM call gets only the context it needs (sectioned extraction)
5. **Budget-aware**: 32K token limit enforced, lowest-priority content truncated first
6. **Checkpoint**: Every stage saves to DB, crash-recoverable
