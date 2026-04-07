# Plan Scorer V2 — Specification

## Problem with Scorer V1

The 6-dimension Sonnet-as-Judge scorer (file_identification, contract_preservation, internal_consistency, codebase_alignment, implementability, scope_calibration) has fundamental flaws:

1. **Rewards incompleteness**: A plan with 1 clean artifact scores higher than a plan with 5 artifacts where 4 are good and 1 has a bug. Run 67 (45.3 avg) had 33% of plans with only 1-2 tiny artifacts.
2. **Scorer drift**: Same plan rescored on different days gives -2.5pts difference.
3. **Abstract dimensions**: "consistency" and "alignment" are vague — the scorer interprets them differently each time.
4. **No structured output**: Free-text justifications make it impossible to aggregate or compare systematically.

## Design Goals

1. **Reward completeness** — more files covered = better, not worse.
2. **Deterministic where possible** — AST-based checks have zero variance.
3. **Per-artifact scoring** — each artifact scored independently, not averaged into abstract dimensions.
4. **Solution taxonomy** — valid architectural approaches enumerated upfront so Sonnet scores against a rubric, not its own judgment.
5. **Reproducible** — same plan always gets the same deterministic score. Sonnet layer adds qualitative assessment but doesn't override deterministic findings.

## Architecture

```
Plan JSON
  │
  ├── Tier 1: Deterministic Checks (zero variance)
  │     ├── Completeness check (which required files are present?)
  │     ├── Per-artifact AST validation
  │     │     ├── Parseable? (syntax errors)
  │     │     ├── Correct field access? (F25 typed attr validation)
  │     │     ├── No fabricated methods? (F10 check against structural index)
  │     │     ├── Has required behavior? (yield for streaming, async for async)
  │     │     └── Correct function signatures? (matches reference)
  │     └── Cross-artifact consistency
  │           ├── Method name agreement (service.answer_stream vs service.query_stream)
  │           └── Type agreement (Iterator[str] vs AsyncGenerator)
  │
  ├── Tier 2: Taxonomy Classification (Sonnet, deterministic rubric)
  │     ├── Which architectural pattern does the plan follow?
  │     ├── Per-artifact: which implementation pattern was used?
  │     └── Scoring against enumerated valid/invalid patterns
  │
  └── Combined Report
        ├── Deterministic score (0-100)
        ├── Taxonomy classification
        ├── Sonnet qualitative notes
        └── Per-artifact breakdown
```

## Tier 1: Deterministic Checks

### 1.1 Completeness Score

For the streaming task, the required files are:

| File | Required? | Why |
|------|-----------|-----|
| `fitz_sage/engines/fitz_krag/engine.py` | YES | `answer_stream()` — the core streaming method |
| `fitz_sage/api/routes/query.py` | YES | `/chat/stream` and/or `/query/stream` endpoints |
| `fitz_sage/engines/fitz_krag/generation/synthesizer.py` | RECOMMENDED | `generate_stream()` — streaming through synthesis |
| `fitz_sage/api/models/schemas.py` | OPTIONAL | Streaming response schema |
| `fitz_sage/sdk/fitz.py` | OPTIONAL | SDK streaming method |
| `fitz_sage/services/*` | OPTIONAL | Service layer delegation |

**Score**: count of required files present / total required. Bonus for recommended/optional files.

For a **codebase-agnostic** version: the required files come from the task's decision decomposition — each decision names files. Files referenced in 3+ decisions are "required."

### 1.2 Per-Artifact AST Validation

For each artifact, run these checks (all deterministic, 0 LLM cost):

| Check | How | Pass/Fail |
|-------|-----|-----------|
| **Parseable** | `ast.parse(content)` — ignore indent errors for code fragments | bool |
| **No fabricated `self.method()`** | Existing `check_artifact` from grounding.py | count of violations |
| **No fabricated `self._xxx.method()`** | Resolve `_xxx` type from init attrs, check method exists | count of violations |
| **No fabricated `request.field`** | F25 typed attr validation | count of violations |
| **No fabricated classes** | Check `ClassName()` constructors against index | count of violations |
| **Has yield** (streaming artifacts) | `yield` keyword present in AST | bool |
| **Has correct return type** | Check function annotation matches purpose | bool |
| **No NotImplementedError** | String check | bool |
| **No sys.stdout** | String check | bool |

**Per-artifact score**: `(checks_passed / total_checks) * 100`

### 1.3 Cross-Artifact Consistency

| Check | How |
|-------|-----|
| **Method name agreement** | If artifact A calls `service.answer_stream()`, artifact B for services must define `answer_stream()` (not `query_stream()`) |
| **Type agreement** | If engine returns `Iterator[str]`, route must consume `Iterator[str]` (not `AsyncGenerator`) |
| **No duplicates** | Same file shouldn't appear with identical content twice |

### 1.4 Deterministic Score Formula

```
completeness = required_files_present / required_files_total  (0-1)
artifact_quality = mean(per_artifact_scores)                   (0-100)
consistency = consistency_checks_passed / total                (0-1)

deterministic_score = (completeness * 30) + (artifact_quality * 0.5) + (consistency * 20)
```

Max = 30 + 50 + 20 = **100 points**.

## Tier 2: Solution Taxonomy

### 2.1 Architecture Taxonomy (task-specific)

For the streaming task, valid architectural patterns:

| ID | Pattern | Quality | Description |
|----|---------|---------|-------------|
| A1 | **Full pipeline + generate_stream** | BEST | `answer_stream()` replicates all pipeline steps, calls `generate_stream()` at the end which wraps `chat_stream()`. All RAG context preserved. |
| A2 | **Full pipeline + direct chat_stream** | GOOD | `answer_stream()` replicates pipeline, calls `self._chat.chat_stream()` at the final step instead of going through synthesizer. Loses synthesis logic but keeps RAG. |
| A3 | **Provider shortcut** | POOR | Calls `chat_stream()` directly, skipping the entire pipeline. No RAG, no context, no guardrails. |
| A4 | **Blocking + split** | BAD | Calls `answer()` then splits `answer.text` into fake tokens. Not real streaming. |
| A5 | **NotImplementedError** | FAIL | Gives up. |

### 2.2 Per-File Implementation Taxonomy

**engine.py:**

| ID | Pattern | Quality |
|----|---------|---------|
| E1 | Full pipeline, yield, correct return type | BEST |
| E2 | Full pipeline, yield, wrong return type (Answer instead of Iterator) | PARTIAL |
| E3 | Partial pipeline (missing steps), yield | PARTIAL |
| E4 | No pipeline, direct chat_stream call | POOR |
| E5 | No yield (returns Answer, blocking) | BAD |
| E6 | NotImplementedError | FAIL |

**routes/query.py:**

| ID | Pattern | Quality |
|----|---------|---------|
| R1 | StreamingResponse + async generator, correct fields | BEST |
| R2 | StreamingResponse, wrong field names (F2/F25) | PARTIAL |
| R3 | StreamingResponse, fabricated service methods | POOR |
| R4 | Not streaming (returns JSON response) | BAD |
| R5 | sys.stdout.flush | BAD |

**synthesizer.py:**

| ID | Pattern | Quality |
|----|---------|---------|
| S1 | `generate_stream()` wrapping `chat_stream()`, correct interface | BEST |
| S2 | Present but raises NotImplementedError | PARTIAL |
| S3 | Absent from plan | ABSENT |

### 2.3 Sonnet's Role with Taxonomy

Sonnet receives:
1. The plan JSON
2. The deterministic check results (Tier 1)
3. The taxonomy tables above
4. The structural index (codebase context)

Sonnet's job:
1. **Classify** each artifact into its taxonomy entry (E1-E6, R1-R5, S1-S3)
2. **Classify** the overall architecture (A1-A5)
3. **Note** any issues the deterministic checks missed (e.g., semantic errors, wrong algorithm choices)
4. **Do NOT** override deterministic findings — if Tier 1 says "3 fabricated methods," Sonnet cannot say "actually it's fine"

### 2.4 Taxonomy Score

```
architecture_score = {A1: 100, A2: 75, A3: 30, A4: 10, A5: 0}
per_file_score = mean of per-file taxonomy scores
taxonomy_score = (architecture_score * 0.4) + (per_file_score * 0.6)
```

## Combined Score

```
final_score = (deterministic_score * 0.6) + (taxonomy_score * 0.4)
```

- 60% weight on deterministic (reproducible, zero variance)
- 40% weight on taxonomy (Sonnet classification, low variance due to rubric)

## Implementation Plan

### Phase 1: Deterministic checker
- Extend `check_artifact` with all Tier 1 checks
- Add completeness check based on decision file references
- Add cross-artifact consistency checks
- Output: structured JSON report

### Phase 2: Taxonomy definition
- Define taxonomy tables for the streaming task
- Make taxonomy format task-agnostic (loaded from JSON/YAML)
- For new tasks: generate taxonomy from an "ideal plan" or from analyzing 5+ plan variants

### Phase 3: Sonnet classifier
- Prompt that receives plan + deterministic results + taxonomy
- Outputs taxonomy classification per artifact + overall
- Structured JSON output (no free text scores)

### Phase 4: Score aggregation
- Combine deterministic + taxonomy scores
- Generate per-plan report card
- Support comparison across runs

## Migration from Scorer V1

- V1 score prompts (`score_prompt_NN.md`) still generated for backwards compat
- V2 runs alongside V1 during transition
- Once V2 is validated on 10+ plans, V1 is removed

## Key Design Decisions

1. **Taxonomy is task-specific** but the FRAMEWORK is task-agnostic. Each task gets its own taxonomy tables. For a new task, generate 5 plans, manually classify them, and the taxonomy emerges.
2. **Deterministic checks are codebase-agnostic** — they use the structural index, not hardcoded patterns.
3. **Sonnet classifies, it doesn't score** — the scoring formula is fixed. Sonnet only picks which taxonomy entry each artifact matches. This eliminates scorer drift.
4. **Completeness is rewarded** — more required files present = higher score. No more penalizing ambitious plans.
