# F25: Unvalidated Local Variable Attribute Access

## Problem
Post-generation validation checks `self.method()` calls against the structural index but completely skips attribute access on local variables (`request.xxx`, `service.xxx`, `answer.xxx`). The AST checker had an explicit comment: "For known variable names like 'request', we'd need type info — This is deferred to the LLM path." But the type info IS available from parameter annotations and the structural index.

## Artifact Post-Generation Validation Map

| # | Artifact state | Example | Handling | Outcome |
|---|---------------|---------|----------|---------|
| 1 | Clean — all refs correct | `request.message`, `self._chat.chat_stream()` | Nothing needed | PASS |
| 2 | Fabricated `self.xxx()` method | `self._chat_provider.stream()` | `_repair_fabricated_refs` + `check_artifact` AST + **retry** | PASS |
| 3 | Fabricated `request.xxx` field | `request.question` on ChatRequest handler | `check_artifact` `wrong_field` + **retry** | **FIXED** |
| 4 | Fabricated function/class call | `TokenDeltaNormalizer()` | `check_artifact` `missing_class` + **retry** | PASS |
| 5 | Wrong import path | `from fitz_sage.service import X` | F5 import repair | PASS |
| 6 | Wrong method on correct object | `self._chat.generate_stream()` | `_repair_fabricated_refs` + `check_artifact` + **retry** | PASS |
| 7 | Fabricated `obj.xxx()` on local var | `service.query_stream()` | Prompt-only (`_resolve_imported_type_apis`), no post-gen check | PARTIAL |
| 8 | Wrong parameter names/count | `service.query(ctx=...)` | `check_artifact` `wrong_arity` + **retry** | PARTIAL |
| 9 | Syntax error in generated code | `""Convert...""` (double quotes) | `check_artifact` `parse_error` + **retry** | **FIXED** |
| 10 | Correct code, wrong file | streaming logic in firstrun.py | No validation | FAIL (semantic) |
| 11 | Bypasses existing layer | Calls chat_stream() directly, skips synthesizer | No validation | FAIL (semantic) |

## Root Causes Found

### 1. Missing type info in structural index
The structural index truncation (Pass 3) dropped low-connectivity files like `schemas.py` to path-only `""`, losing all class/field info. The `StructuralIndexLookup` had no `ChatRequest` entry → validation couldn't check `request.xxx`.

**Fix**: Indexer now extracts Pydantic `AnnAssign` fields. Truncation now strips `doc:` lines before `classes:` lines, and keeps `classes:` line even in last-resort truncation. `ChatRequest [message, history, collection, top_k]` now survives.

### 2. Per-function scope needed for type resolution
The initial implementation built ONE `var_type_map` from ALL functions in the artifact. When an artifact had both `_stream_query(request: QueryRequest)` and `_stream_chat(request: ChatRequest)`, the second annotation overwrote the first — so `request.question` in the QueryRequest handler was flagged as a violation.

**Fix**: Type maps are now built per-function scope. Each function's parameter annotations only apply within that function's body.

### 3. No retry on ANY violations (not just wrong_field)
The initial `_generate_single_artifact_checked` only retried on `wrong_field` violations. But the 2 remaining faulty plans had **syntax errors** (`""double quotes""` instead of `"""triple quotes"""`), which produced `parse_error` violations that were ignored.

**Fix**: Retry fires on ALL violation kinds: `parse_error`, `missing_method`, `missing_class`, `missing_function`, `wrong_arity`, `wrong_field`.

### 4. Gatherer imported indexer from target codebase
`gatherer.py` imported `build_structural_index` from `fitz_sage.code.indexer` (the target codebase) instead of `fitz_forge.planning.agent.indexer` (our own). Our fixes to the indexer had no effect.

**Fix**: Changed import to use fitz_forge's own indexer.

## Measurements

| Run | Fix state | Wrong field rate (query.py artifacts) |
|-----|-----------|---------------------------------------|
| 74 (before) | No fix | 4/5 (80%) |
| 75 (partial) | Indexer fix but wrong import | 2/4 (50%) — detection worked when index happened to have data |
| 76 (full but wrong_field only) | All fixes but retry only on wrong_field | 2/5 (40%) — syntax errors bypassed retry |
| 77 (pending) | All fixes, retry on all violations | TBD |

## Status: 🟡 PARTIALLY FIXED — detection works, retry on all violations, but upstream reasoning contamination means ~40% of artifacts still fail both attempts
