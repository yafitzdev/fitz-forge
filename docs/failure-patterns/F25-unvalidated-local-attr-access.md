# F25: Unvalidated Local Variable Attribute Access

## Problem
Post-generation validation checks `self.method()` calls against the structural index but completely skips attribute access on local variables (`request.xxx`, `service.xxx`, `answer.xxx`). The AST checker has an explicit comment: "For known variable names like 'request', we'd need type info — This is deferred to the LLM path." But the type info IS available from parameter annotations and the structural index.

## Artifact Post-Generation Validation Map

| # | Artifact state | Example | Current handling | Outcome |
|---|---------------|---------|-----------------|---------|
| 1 | Clean — all refs correct | `request.message`, `self._chat.chat_stream()` | Nothing needed | PASS |
| 2 | Fabricated `self.xxx()` method | `self._chat_provider.stream()` | `_repair_fabricated_refs` + `check_artifact` AST | PASS |
| 3 | **Fabricated `request.xxx` field** | `request.question` on ChatRequest handler | **Nothing — AST checker skips** | **FAIL** |
| 4 | Fabricated function/class call | `TokenDeltaNormalizer()` | `check_artifact` AST catches | PASS |
| 5 | Wrong import path | `from fitz_sage.service import X` | F5 import repair | PASS |
| 6 | Wrong method on correct object | `self._chat.generate_stream()` | `_repair_fabricated_refs` + `check_artifact` | PASS |
| 7 | **Fabricated `obj.xxx()` on local var** | `service.query_stream()` | **Prompt-only, no post-gen check** | **FAIL** |
| 8 | Wrong parameter names/count | `service.query(ctx=...)` | Arity check only, no keyword validation | PARTIAL |
| 9 | Syntax error in generated code | `"n        except Exception"` | `check_artifact` returns parse_error, **not retried** | PARTIAL |
| 10 | Correct code, wrong file | streaming logic in firstrun.py | No validation | FAIL (semantic) |
| 11 | Bypasses existing layer | Calls chat_stream() directly, skips synthesizer | No validation | FAIL (semantic) |

Cases 3 and 7 are deterministically fixable. Cases 10 and 11 are semantic errors requiring LLM judgment.

## Root Cause (cases 3 and 7)
In `fitz_forge/planning/validation/grounding.py`, `_check_node()` at line ~480:
```python
# For known variable names like 'request', we'd need type info
# This is deferred to the LLM path
```

The type info is available:
- **Parameter annotations**: `def chat_stream(request: ChatRequest)` — we know `request` is `ChatRequest`
- **Variable assignments**: `service = get_service()` — return type resolvable from index
- **Structural index**: has `ChatRequest fields: message, history, collection`

## Examples from run 73
- Plan 73b: `request.question` and `request.conversation_history` in a `ChatRequest` handler (real: `message`, `history`)
- Plan 73c: `request.messages` instead of `request.history`, `request.source` (nonexistent)
- Plan 73b: `service.query_stream()` — FitzService has no `query_stream` method

## Occurrence
- Case 3 (request fields): 2/2 query.py artifacts (100% when route artifact is generated)
- Case 7 (service methods): present but partially covered by `_resolve_imported_type_apis` prompt injection

## Impact
~2-3 pts on alignment + implementability. Generated code would crash with AttributeError at runtime.

## Fix Plan
Add to `_check_node()` in `check_artifact`:
1. When encountering `varname.attr` access, resolve `varname`'s type from:
   - Function parameter annotations (AST `arg.annotation`)
   - Variable assignments with known constructors/calls
2. Look up the resolved type in the structural index
3. Check if `attr` exists as a field or method on that type
4. If not: emit a `wrong_field` or `missing_method` violation
5. In the repair step: replace with the closest matching field from the correct type

## Status: ❌ Not yet fixed
