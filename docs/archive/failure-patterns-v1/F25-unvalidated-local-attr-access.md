# F25: Wrong Field Access on Typed Local Variables

## The Bug (with real example)

The model generates a `_stream_chat(request: ChatRequest)` handler but uses `request.question` instead of `request.message`. `question` is a field on `QueryRequest`, not `ChatRequest`. The generated code would crash with `AttributeError` at runtime.

### Real source code (what the model sees in prompt)
```python
# /query handler — uses QueryRequest fields
async def query(request: QueryRequest) -> QueryResponse:
    answer = service.query(
        question=request.question,              # QueryRequest.question ✓
        conversation_context=_to_conversation_context(request.conversation_history),  # QueryRequest ✓
    )

# /chat handler — uses ChatRequest fields  
async def chat(request: ChatRequest) -> ChatResponse:
    answer = service.query(
        question=request.message,               # ChatRequest.message ✓
        conversation_context=_to_conversation_context(request.history),  # ChatRequest ✓
    )
```

### What the model generates
```python
# _stream_query — correct, copies from /query handler
async def _stream_query(request: QueryRequest):
    answer = service.query(
        question=request.question,              # ✓ correct for QueryRequest
        conversation_context=_to_conversation_context(request.conversation_history),  # ✓
    )

# _stream_chat — WRONG, copies from _stream_query instead of /chat handler
async def _stream_chat(request: ChatRequest):
    answer = service.query(
        question=request.question,              # ✗ WRONG — should be request.message
        conversation_context=_to_conversation_context(request.history),  # ✓ this one was fixed
    )
```

The model writes `_stream_query` first (correct), then copies its body for `_stream_chat` and partially fixes the field names. It changes `conversation_history` → `history` but misses `question` → `message`.

## Why it happens

1. The source file has both `/query` and `/chat` handlers. `/query` comes first.
2. The task says "query result streaming" — the word "query" primes the model.
3. The model generates `_stream_query` first (copying from the `/query` handler).
4. Then it generates `_stream_chat` by copying from its own `_stream_query` output instead of from the real `/chat` handler in the source code.
5. It partially fixes fields (history is fixed, question is not) — classic attention drift in long generation.

## Why retry doesn't work

The retry regenerates the artifact from the **same prompt** with the same reasoning. The reasoning upstream already contains references to `request.question` for the streaming endpoint. The model sees "use request.question" in the reasoning context and follows it, even though the source code and schema fields section show the correct field name. With temperature=0.7, the retry produces slightly different code but the same wrong fields ~80% of the time.

## What we've done so far

### Detection (WORKS)
1. **Indexer**: extracts Pydantic `AnnAssign` fields → `ChatRequest [message, history, collection, top_k]` in structural index
2. **Truncation**: preserves `classes:` line even for low-connectivity files (schemas.py was being dropped to path-only)
3. **check_artifact**: resolves parameter type annotations per-function scope, validates `request.xxx` against the type's fields
4. **Import fix**: gatherer now uses fitz_forge's own indexer (was importing fitz_sage's copy which didn't have our fixes)

Detection catches 100% of wrong-field violations on actual faulty artifacts.

### Repair (DOESN'T WORK)
- **Retry**: same prompt → same contaminated reasoning → same wrong fields (~80% of the time)
- **Fuzzy string match**: `difflib.get_close_matches("question", ["message", "history", "collection", "top_k"])` returns `collection`, not `message` — character similarity doesn't map to semantic correspondence
- **LLM repair**: historically unreliable (bisect showed it makes wrong corrections)

## What needs to happen

The detection works. The repair doesn't. Options:

1. **Cross-model field mapping**: if `question` is wrong on `ChatRequest` but correct on `QueryRequest`, and the source code shows `/chat` uses `request.message` where `/query` uses `request.question` — extract this mapping from the source and apply it. Deterministic, uses actual codebase as ground truth.

2. **Scope the source in the prompt**: instead of giving the full 106-line file with both handlers, inject only the `/chat` handler source when generating a `/chat/stream` artifact. Remove the source of confusion.

3. **Fix the reasoning**: the contamination starts in the synthesis reasoning, which says "use request.question for the streaming endpoint." If the reasoning used correct field names, the artifact would too. This means validating the reasoning text, not just the artifact.

## Occurrence
- Run 74 (before fix): 4/5 route artifacts had wrong fields (80%)
- Run 76 (with detection + retry): 2/5 (40%) — retry helped on syntax errors, not on field contamination
- Affects alignment (-2 pts) and implementability (-1 pt) per plan

## Final Fix: Per-Function Artifact Decomposition

The retry approach failed because the same contaminated reasoning produces the same wrong fields. The real fix was **decomposing file-level artifacts into per-function artifacts**.

### How it works
1. `_decompose_multi_handler_artifacts` runs after `needed_artifacts` extraction
2. Parses source file AST → finds route handlers via `@router.post("/path")` decorators
3. Finds new endpoint paths in decisions (e.g., `/query/stream`, `/chat/stream`)
4. Matches each new endpoint to its base handler: `/chat/stream` → `/chat` → `chat()`
5. Splits into separate artifacts: one per new function

### Result
Each artifact gets a focused purpose like "streaming variant of chat()". `_extract_reference_method` then correctly picks `chat()` (with `request.message`) instead of `query()` (with `request.question`). The model can't copy from the wrong handler because it's not in the prompt.

### Before/After
- **Before (run 74)**: 5/6 route artifacts had wrong fields (83%) — AST-confirmed
- **After (run 77)**: 0/7 route artifacts had wrong fields (0%) on decomposed plans
- **Run 77 scored 31.8** — regression from inflated index (172K vs 119K), NOT from decomposition
- **Run 79 scored 38.8** — back to baseline after dual-index fix. Net score impact: **neutral** (wrong fields fixed but other dimensions unchanged)

### Additional fixes needed after initial decomposition
1. Decomposition regex: also match `xxx_stream` function names, not just `/xxx/stream` paths
2. Reference method extraction: search purpose first, decisions as fallback — decisions mention all functions and override the decomposed purpose
3. Dual index: fitz_sage's indexer for LLM context (120K budget), fitz_forge's for validation (untruncated with Pydantic fields)

## Status: ✅ FIXED — per-function decomposition eliminates wrong field access. Overall score impact neutral (38.8 vs 41 baseline, within noise).
