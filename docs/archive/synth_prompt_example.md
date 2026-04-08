# Actual Synthesis Prompt (real example)

**Total chars:** 54784
**Estimated tokens:** ~13696

## System message (358 chars)



## User message (54784 chars)

You are writing a comprehensive architectural plan. All the hard decisions have already been made and resolved below. Your job is to narrate these decisions into a coherent, complete plan.

TASK: Add query result streaming so answers are delivered token-by-token instead of waiting for the full response



## Resolved Decisions

The following decisions were made by analyzing the actual source code. Each decision includes evidence and constraints. DO NOT contradict these decisions -- they are based on ground truth from the codebase.

### Decision d1
**Decided:** The return type contract for streaming chat responses across all provider implementations is `Iterator[str]`, and existing `chat_stream` methods yield tokens by yielding one string at each iteration — specifically, each yielded value corresponds to a single token (or token fragment) from the LLM response stream.
**Evidence:**
  - fitz_sage/llm/providers/base.py: class StreamingChatProvider(Protocol): def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]: ...
  - fitz_sage/llm/providers/openai.py: class OpenAIChat: def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]: ...
  - fitz_sage/llm/providers/anthropic.py: class AnthropicChat: def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]: ...
**Constraints:**
  - Any new streaming-capable provider must implement `chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]` exactly matching this signature
  - The existing non-streaming `chat()` method must remain unchanged and continue to return `str`, not be replaced or aliased
  - All callers of `chat_stream()` must consume the result as an `Iterator[str]`, expecting one token per iteration step

### Decision d2
**Decided:** `FitzKragEngine.answer()` currently consumes chat provider responses by calling `self._chat_provider.chat(messages, **kwargs)` (a blocking method returning `str`), and must be changed to instead call `self._chat_provider.chat_stream(messages, **kwargs)` (a streaming method returning `Iterator[str]`) and yield tokens incrementally via a generator that constructs the final `Answer` object token-by-token.
**Evidence:**
  - fitz_sage/engines/fitz_krag/engine.py: def answer(self, query: Query, *, progress: Callable[[str], None] | None = None) -> Answer
  - fitz_sage/engines/fitz_krag/engine.py: class FitzKragEngine: ... # 331 lines in answer()
  - CONSTRAINTS FROM PREVIOUS DECISIONS: Any new streaming-capable provider must implement `chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]` exactly matching this signature
**Constraints:**
  - `FitzKragEngine.answer()` public method signature `answer(self, query: Query, *, progress: Callable[[str], None] | None = None) -> Answer` must remain unchanged — no change to parameter names/types or return type
  - The internal implementation of `answer()` may call `_chat_provider.chat_stream(...)` instead `_chat_provider.chat(...)`, but must still produce a single `Answer` object as final output (i.e., accumulate tokens internally before returning)
  - `_chat_provider.chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]` must be used only by callers that consume it as an `Iterator[str]`, expecting one token per iteration step — no buffering or pre-collation allowed at the provider side
  - existing method `_chat_provider.chat()` must not be modified or removed — it remains unchanged and returns `str`

### Decision d6
**Decided:** All provider `chat_stream` implementations yield raw text tokens (i.e., `Iterator[str]`), and error handling mid-stream is is not implemented — the methods are stubbed (`...`) with no actual implementation, so behavior is undefined for errors during streaming.
**Evidence:**
  - fitz_sage/llm/providers/openai.py: OpenAIChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/llm/providers/cohere.py: CohereChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/llm/providers/ollama.py: OllamaChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
**Constraints:**
  - All new provider implementations of `chat_stream()` must preserve the exact signature `def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]`
  - `chat_stream()` callers must consume result as `Iterator[str]`, expecting one raw text token per iteration step — no structured chunks or metadata allowed
  - existing method `chat()` must not be modified — it remains unchanged and returns `str`
  - no error-handling semantics are defined in current code, so downstream implementations must decide how to handle mid-stream errors (e.g., raise exception on first failed chunk, suppress, or log-and-continue) — but this decision is deferred until implementation

### Decision d12
**Decided:** The `chat()` and `chat_stream()` methods in each provider class (`OpenAIChat`, `AnthropicChat`) are implemented as *independent, non-delegating* implementations — they do not share a common internal implementation. Neither method calls the other internally.
**Evidence:**
  - fitz_sage/llm/providers/openai.py: OpenAIChat.chat(messages: list[dict[str, Any]], **kwargs: Any) -> str
  - fitz_sage/llm/providers/openai.py: OpenAIChat.chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/llm/providers/anthropic.py: AnthropicChat.chat(messages: list[dict[str, Any]], **kwargs: Any) -> str
  - fitz_sage/llm/providers/anthropic.py: AnthropicChat.chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
**Constraints:**
  - existing method `chat()` must not be modified — its signature and implementation remain unchanged (must continue to return `str`)
  - new method `chat_stream()` must retain exact signature: `(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]` in every provider
  - no internal delegation between `chat()` and `chat_stream()` may be introduced — both must remain independently implemented to preserve caller independence and avoid breaking changes

### Decision d3
**Decided:** The current return type of `FitzKragEngine.answer()` is `Answer`, and it carries streaming state only after full accumulation — i.g., tokens are not exposed individually; the `Answer` object contains only the final concatenated `text` string, with no field for intermediate tokens or finish_reason. Streaming state (tokens, finish_reason) must be accumulated internally by `answer()` before constructing the final `Answer`.
**Evidence:**
  - fitz_sage/core/answer.py: @dataclass\nclass Answer:\n    text: str\n    provenance: List[Provenance] = field(default_factory=list)\n    mode: Optional["AnswerMode"] = None\n    metadata: Dict[str, Any] = field(default_factory=dict)
  - fitz_sage/core/answer.py: def __post_init__(self):\n        if self.text is None:\n            raise ValueError("Answer text cannot be None (use empty string for no answer)")
**Constraints:**
  - `FitzKragEngine.answer()` public method signature `answer(self, query: Query, *, progress: Callable[[str], None] | None = None) -> Answer` must remain unchanged — no change to parameter names/types or return type
  - The internal implementation of `answer()` may call `_chat_provider.chat_stream(...)` but must still produce a single `Answer` object as final output (i.g., accumulate tokens internally before returning)
  - `_chat_provider.chat_stream(...)` returns `Iterator[str]`, and callers must consume it token-by-token — no buffering or pre-collation at provider side
  - The existing method `_chat_provider.chat()` must not be modified or removed — it remains unchanged and returns `str`
  - The `Answer` dataclass cannot be extended to expose streaming state (tokens, finish_reason) unless stored in the `metadata` dict — but only if explicitly allowed by design; no such requirement is stated in current source

### Decision d10
**Decided:** No error handling semantics are defined or implied in current `chat_stream()` implementations — both `openai.py` and `ollama.py` declare `chat_stream()` with signature `def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]`, but their bodies are elided (`...`) and no error handling behavior (e.g., exception propagation, suppression, logging) is specified or observable in the source. Therefore, this decision defers to implementation time: downstream implementations may choose any strategy (raise on first failure, suppress, log-and-continue), but must preserve the `Iterator[str]` contract — i.e., yield only raw text tokens and never yield exceptions or structured error objects.
**Evidence:**
  - fitz_sage/llm/providers/openai.py: OpenAIChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]:
  - fitz_sage/llm/providers/ollama.py: OllamaChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]:
  - fitz_sage/llm/providers/ollama.py: def _check_ollama_response(response: httpx.Response, model: str) -> None: ...  # 16 lines
**Constraints:**
  - All implementations of `chat_stream()` must yield only raw text tokens via `Iterator[str]` — no exceptions, error objects, or metadata chunks may be yielded
  - new provider implementations must preserve the exact signature `def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]`
  - downstream implementations may define their own error handling (e.g., raise on first chunk failure), but this decision does not standardize or mandate any strategy — it remains open to implementation choice
  - existing method `chat()` must not be modified — it remains unchanged and returns `str`

### Decision d4
**Decided:** The `/query` endpoint currently returns a `QueryResponse` by calling `engine.answer()` synchronously and serializing its result; to enable token-by-token streaming, a new parallel endpoint `/stream_query` must be introduced that accepts the same `QueryRequest`, calls a *new* async streaming method (e.g., `engine.answer_stream(...)`) that yields tokens via `_chat_provider.chat_stream(...)`, and returns them using FastAPI’s `StreamingResponse`. The existing `/query` endpoint must remain unchanged — no modification to its signature, implementation, or return type.
**Evidence:**
  - fitz_sage/api/routes/query.py: @router.post('/query', response_model=QueryResponse) async def query(request: QueryRequest) -> QueryResponse
  - fitz_sage/api/routes/query.py: ...  # 28 lines (no visible implementation but endpoint returns QueryResponse)
  - constraint: `FitzKragEngine.answer(self, query: Query, *, progress: Callable[[str], None] | None = None) -> Answer` must remain unchanged
**Constraints:**
  - The `/query` endpoint must not be modified — its signature `async def query(request: QueryRequest) -> QueryResponse`, decorator `@router.post('/query', response_model=QueryResponse)`, and implementation remain unchanged
  - A new streaming route (e.g., `/stream_query`) must be added alongside `/query`; it cannot reuse or alter the existing `/query` endpoint
  - The engine’s `answer()` method signature and behavior are frozen — no changes allowed; a *new* method (e.g., `answer_stream(...)`) would need to be introduced in `FitzKragEngine`, but its design is not specified here and must be decided separately

### Decision d5
**Decided:** The `Answer` dataclass returned by `fitz.query()` is a *static* object with no support for incremental updates — it only exposes `text`, `provenance`, `mode`, and `metadata`. Incremental token delivery must be be implemented via a *new parallel streaming API*, because the current `answer()` method (in `FitzKragEngine`) must return a single fully-formed `Answer` object, and its signature cannot change. The existing `fitz.query()` method also returns `Answer`, so it cannot support streaming without breaking its contract.
**Evidence:**
  - fitz_sage/core/answer.py: @dataclass class Answer(text: str, provenance: List[Provenance] = field(default_factory=list), mode: Optional['AnswerMode'] = None, metadata: Dict[str, Any] = field(default_factory=dict))
  - fitz_sage/sdk/fitz.py: def query(self, question: str, source: Optional[Union[str, Path]] = None, top_k: Optional[int] = None, conversation_context: Optional['ConversationContext'] = None) -> Answer
  - fitz_sage/core/answer.py: def __post_init__(self): if self.text is None: raise ValueError(...)
**Constraints:**
  - `fitz.query()` method signature and return type (`Answer`) must remain unchanged — no new parameters (e.g., `stream: bool`) or return type changes
  - A new streaming API (e.g., `query_stream(...) -> Iterator[str]` or similar) must be added as a *parallel* method alongside `query()`, not replace it
  - `Answer` dataclass cannot be extended to expose token streams unless stored in `metadata`, and even then only if explicitly allowed — but no such allowance is stated, so assume metadata is opaque for now

### Decision d9
**Decided:** Caching via `_check_cloud_cache()` and `_store_cloud_cache()` must be skipped entirely when streaming is enabled — i.e., when `answer()` uses `_chat_provider.chat_stream()` instead `_chat_provider.chat()`. Streaming responses cannot be cached as a full `Answer` until the entire token stream has been consumed, and `_store_cloud_cache()` expects a complete `Answer`, not partial tokens.
**Evidence:**
  - fitz_sage/engines/fitz_krag/engine.py: FitzKragEngine._check_cloud_cache(self, query_text: str, addresses: list) -> Answer | None
  - fitz_sage/engines/fitz_krag/engine.py: FitzKragEngine._store_cloud_cache(self, query_text: str, addresses: list, answer: Answer) -> None
  - fitz_sage/engines/fitz_krag/engine.py: FitzKragEngine.answer(self, query: Query, *, progress: Callable[[str], None] | None = None) -> Answer
  - fitz_sage/engines/fitz_krag/engine.py: _chat_provider.chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
**Constraints:**
  - FitzKragEngine._check_cloud_cache() and _store_cloud_cache() must not be modified — they remain unchanged and assume full Answer input/output
  - When answer() uses _chat_provider.chat_stream(), it must NOT call _check_cloud_cache() or _store_cloud_cache() — i.e., cloud caching must be bypassed for streaming paths
  - FitzKragEngine.answer() public method signature must remain unchanged — no change to parameter names/types or return type
  - new internal branching (e.g., streaming vs non-streaming) must not affect the external contract of answer(), _check_cloud_cache(), or _store_cloud_cache()

### Decision d7
**Decided:** The instrumentation system CANNOT wrap generator functions like `chat_stream` because `InstrumentedProxy._wrap_method()` returns a regular function that calls the original method and then processes its *return value* as a single result; it does not handle or preserve generator semantics (i.e., `yield`/`Iterator` behavior), so wrapping a generator would cause it to be consumed immediately upon first call, losing streaming semantics.
**Evidence:**
  - core/instrumentation.py: InstrumentedProxy._wrap_method(method: Callable, method_name: str) -> Callable
  - core/instrumentation.py: BenchmarkHook.on_call_end(context: Any, result: Any, error: Exception | None) -> None
  - core/instrumentation.py: maybe_wrap(target: Any, layer: str, plugin_name: str, methods_to_track: set[str] | None = None) -> Any
**Constraints:**
  - InstrumentedProxy cannot be used to instrument per-token streaming of generators like chat_stream() — any instrumentation must be applied outside the generator, e.g., at a higher-level caller that consumes tokens and reports progress
  - new_method() (e.g., answer_stream()) must avoid using InstrumentedProxy on the underlying chat_stream() call if per-token hooking is needed; hooks will only see the generator object as result, not individual tokens

### Decision d8
**Decided:** New streaming response types MUST be introduced — specifically, a new endpoint `/stream_query` returning `StreamingResponse` (FastAPI's built-in for Server-Sent Events or chunked transfer) must be added alongside the existing `/query`, and a parallel `chat_stream()` endpoint must also be added for `/chat`. The current `QueryResponse` and `ChatResponse` schemas remain unchanged as their contracts are frozen by the existing route signatures.
**Evidence:**
  - fitz_sage/api/routes/query.py: @router.post('/query', response_model=QueryResponse) async def query(request: QueryRequest) -> QueryResponse
  - fitz_sage/api/routes/query.py: @router.post('/chat', response_model=ChatResponse) async def chat(request: ChatRequest) -> ChatResponse
**Constraints:**
  - existing method query(request: QueryRequest) -> QueryResponse must not be modified — its signature and return type are frozen
  - existing method chat(request: ChatRequest) -> ChatResponse must not be modified — its signature and return type are frozen
  - new streaming endpoints (e.g., /stream_query, /chat_stream) must use FastAPI's StreamingResponse or equivalent async generator–based response type, NOT QueryResponse or ChatResponse
  - new streaming methods must accept the same parameters as their original (e.g., request: QueryRequest for stream_query), but may add optional flags like stream=True if needed — but only only if engine supports it

### Decision d11
**Decided:** {
  "decision_id": "d11",
  "decision": "The `Answer` dataclass does not support incremental token accumulation — a new streaming answer wrapper must be introduced.",
  "reasoning": "The `Answer` dataclass in `core/answer.py` is defined with a single `text: str` field, and no mechanism for incremental updates or partial content. Its `__post_init__` enforces that `text` is non-None but does not allow for deferred or streaming population of `text`. The constraint requires that `FitzKragEngine.answ

### Decision d14
**Decided:** The instrumentation hook system does NOT support generator functions — `InstrumentedProxy._wrap_method` MUST be extended to handle generators.
**Evidence:**
  - fitz_sage/core/instrumentation.py: BenchmarkHook.on_call_end(context: Any, result: Any, error: Exception | None) -> None
  - fitz_sage/core/instrumentation.py: InstrumentedProxy._wrap_method(method: Callable, method_name: str) -> Callable
  - fitz_sage/core/instrumentation.py: InstrumentedProxy.__getattr__(self, name: str) -> Any
**Constraints:**
  - existing method `InstrumentedProxy._wrap_method(method: Callable, method_name: str) -> Callable` must not be modified — its signature and behavior for non-generator methods is preserved
  - new generator-aware instrumentation must be added via a parallel mechanism (e.g., `_wrap_generator_method`) to avoid breaking existing callers that expect synchronous or non-streaming return types
  - `BenchmarkHook.on_call_end(...)` cannot be retrofitted to yield per-token events — hooks will only see the generator object as `result`, so token-level instrumentation must be implemented outside the hook system (e.g., in a wrapper around the generator consumption loop)

### Decision d13
**Decided:** The streaming endpoint MUST be be made async with `StreamingResponse` using an async generator — FastAPI requires async endpoints to stream responses, and the existing sync-style async functions (`async def query(...) -> QueryResponse`) cannot yield tokens without changing their return type, which is forbidden.
**Evidence:**
  - fitz_sage/api/routes/query.py: async def query(request: QueryRequest) -> QueryResponse
  - fitz_sage/api/routes/query.py: async def chat(request: ChatRequest) -> ChatResponse
**Constraints:**
  - existing method query(request: QueryRequest) -> QueryResponse must not be modified — its signature and return type are frozen
  - existing method chat(request: ChatRequest) -> ChatResponse must not be modified — its signature and return type are frozen
  - new streaming endpoints (e.g., /stream_query, /chat_stream) must use FastAPI's StreamingResponse with an async generator function (i.e., `async def ... -> StreamingResponse`), NOT QueryResponse or ChatResponse
  - new streaming methods must accept the same parameters as their original (e.g., request: QueryRequest for stream_query), but may add optional flags only if engine supports them

### Decision d15
**Decided:** Yes — streaming requires preserving conversation state across token chunks because `fitz.query()` accepts `conversation_context` (type `Optional[ConversationContext]`) and passes it to the underlying service, meaning each token chunk must be associated with the same conversation context to maintain continuity.
**Evidence:**
  - fitz_sage/sdk/fitz.py: fitz.query(question: str, source: Optional[Union[str, Path]] = None, top_k: Optional[int] = None, conversation_context: Optional['ConversationContext'] = None) -> Answer
  - fitz_sage/sdk/fitz.py: def __init__(self, collection: str = 'default', config_path: Optional[Union[str, Path]] = None, auto_init: bool = True) -> None
**Constraints:**
  - new_method() must accept identical parameters to query() — including `conversation_context` — to preserve contract compatibility
  - existing method fitz.query(question: str, source: Optional[Union[str, Path]] = None, top_k: Optional[int] = None, conversation_context: Optional['ConversationContext'] = None) -> Answer must not be modified


## Codebase Context

--- INTERFACE SIGNATURES (auto-extracted, ground truth) ---
## fitz_sage/llm/providers/base.py
class RerankResult:
class ChatProvider(Protocol):
  chat(messages: list[dict[str, Any]]) -> str
class StreamingChatProvider(Protocol):
  chat_stream(messages: list[dict[str, Any]]) -> Iterator[str]
class EmbeddingProvider(Protocol):
  embed(text: str) -> list[float]
  embed_batch(texts: list[str]) -> list[list[float]]
  dimensions() -> int
class RerankProvider(Protocol):
  rerank(query: str, documents: list[str], top_n: int | None) -> list[RerankResult]
class VisionProvider(Protocol):
  describe_image(image_base64: str, prompt: str | None) -> str

## fitz_sage/llm/providers/openai.py
class OpenAIChat:
  __init__(auth: AuthProvider, model: str | None, tier: ModelTier, base_url: str | None, models: dict[ModelTier, str] | None) -> None
  chat(messages: list[dict[str, Any]]) -> str
  chat_stream(messages: list[dict[str, Any]]) -> Iterator[str]
class OpenAIEmbedding:
  __init__(auth: AuthProvider, model: str | None, dimensions: int | None, base_url: str | None) -> None
  embed(text: str) -> list[float]
  embed_batch(texts: list[str]) -> list[list[float]]
  dimensions() -> int
class OpenAIVision:
  __init__(auth: AuthProvider, model: str | None, base_url: str | None) -> None
  describe_image(image_base64: str, prompt: str | None) -> str

## fitz_sage/llm/providers/anthropic.py
_extract_system_message(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]
class AnthropicChat:
  __init__(auth: AuthProvider, model: str | None, tier: ModelTier, models: dict[ModelTier, str] | None) -> None
  chat(messages: list[dict[str, Any]]) -> str
  chat_stream(messages: list[dict[str, Any]]) -> Iterator[str]
class AnthropicVision:
  __init__(auth: AuthProvider, model: str | None) -> None
  describe_image(image_base64: str, prompt: str | None) -> str

## fitz_sage/llm/providers/cohere.py
class CohereChat:
  __init__(auth: AuthProvider, model: str | None, tier: ModelTier, models: dict[ModelTier, str] | None) -> None
  chat(messages: list[dict[str, Any]]) -> str
  chat_stream(messages: list[dict[str, Any]]) -> Iterator[str]
class CohereEmbedding:
  __init__(auth: AuthProvider, model: str | None, input_type: str, dimensions: int | None) -> None
  embed(text: str) -> list[float]
  embed_batch(texts: list[str]) -> list[list[float]]
  _embed_single_batch(texts: list[str], input_type: str | None) -> list[list[float]]
  dimensions() -> int
class CohereRerank:
  __init__(auth: AuthProvider, model: str | None) -> None
  rerank(query: str, documents: list[str], top_n: int | None) -> list[RerankResult]

## fitz_sage/llm/providers/ollama.py
_check_ollama_response(response: httpx.Response, model: str) -> None
class OllamaChat:
  __init__(model: str | None, tier: ModelTier, base_url: str | None, models: dict[ModelTier, str] | None) -> None
  chat(messages: list[dict[str, Any]]) -> str
  chat_stream(messages: list[dict[str, Any]]) -> Iterator[str]
  __del__() -> None
class OllamaEmbedding:
  __init__(model: str | None, base_url: str | None, num_ctx: int | None) -> None
  _resolve_prefixes() -> dict[str, str]
  _apply_prefix(text: str, task_type: str | None) -> str
  embed(text: str) -> list[float]
  embed_batch(texts: list[str]) -> list[list[float]]
  dimensions() -> int
  __del__() -> None
class OllamaRerank:
  __init__(model: str | None, base_url: str | None) -> None
  rerank(query: str, documents: list[str], top_n: int | None) -> list[RerankResult]
  _score_document(query: str, document: str) -> float
  _parse_score(content: str) -> float
  __del__() -> None
class OllamaVision:
  __init__(model: str | None, base_url: str | None) -> None
  describe_image(image_base64: str, prompt: str | None) -> str
  __del__() -> None

## fitz_sage/core/instrumentation.py
class BenchmarkHook(Protocol):
  on_call_start(layer: str, plugin_name: str, method: str, args: tuple, kwargs: dict) -> Any
  on_call_end(context: Any, result: Any, error: Exception | None) -> None
class CachingHook(Protocol):
  get_cached_result(layer: str, plugin_name: str, method: str, args: tuple, kwargs: dict) -> Any
  cache_result(layer: str, plugin_name: str, method: str, args: tuple, kwargs: dict, result: Any) -> None
register_hook(hook: BenchmarkHook) -> None
unregister_hook(hook: BenchmarkHook) -> None
clear_hooks() -> None
has_hooks() -> bool
get_hooks() -> list[BenchmarkHook]
class InstrumentedProxy:
  __init__(target: Any, layer: str, plugin_name: str, methods_to_track: set[str] | None)
  __getattr__(name: str) -> Any
  _wrap_method(method: Callable, method_name: str) -> Callable
  __repr__() -> str
  __str__() -> str
maybe_wrap(target: Any, layer: str, plugin_name: str, methods_to_track: set[str] | None) -> Any
wrap(target: Any, layer: str, plugin_name: str, methods_to_track: set[str] | None) -> InstrumentedProxy

## fitz_sage/logging/logger.py
configure_logging(level: int, fmt: str, stream)
get_logger(name: str) -> StructuredLogger

## fitz_sage/engines/fitz_krag/engine.py
_report_timings(progress: Callable[[str], None], timings: list[tuple[str, float]], pipeline_start: float) -> None
class FitzKragEngine:
  __init__(config: FitzKragConfig)
  load(collection: str) -> None
  _wire_agentic_strategy() -> None
  _try_load_persisted_manifest(collection: str) -> None
  _init_components() -> None
  _needs_detection(query: str) -> bool
  _build_detection_summary(results: dict, query: str) -> Any
  _fast_analyze(query: str) -> 'QueryAnalysis | None'
  answer(query: Query) -> Answer
  _build_conflict_context(constraint_results: list) -> dict | None
  _build_gap_context(query: str, governance_reasons: tuple[str, ...]) -> dict
  _check_cloud_cache(query_text: str, addresses: list) -> Answer | None
  _store_cloud_cache(query_text: str, addresses: list, answer: Answer) -> None
  _build_cache_versions() -> Any
  point(source: Path, collection: str | None) -> Any

## fitz_sage/sdk/fitz.py
class fitz:
  __init__(collection: str, config_path: Optional[Union[str, Path]], auto_init: bool) -> None
  collection() -> str
  config_path() -> Path
  query(question: str, source: Optional[Union[str, Path]], top_k: Optional[int], conversation_context: Optional['ConversationContext']) -> Answer
  _ensure_config() -> None

## fitz_sage/api/routes/query.py
_to_conversation_context(history: list[ChatMessage]) -> ConversationContext | None
async query(request: QueryRequest) -> QueryResponse
async chat(request: ChatRequest) -> ChatResponse

--- LIBRARY API REFERENCE (installed packages, ground truth) ---
## argparse
class Action: format_usage(self)
class ArgumentDefaultsHelpFormatter: add_argument(self, action), add_arguments(self, actions), add_text(self, text), add_usage(self, usage, actions, groups, prefix=None), end_section(self), format_help(self), start_section(self, heading)
class ArgumentError: add_note(), with_traceback()
class ArgumentParser: add_argument(self, *args, **kwargs), add_argument_group(self, *args, **kwargs), add_mutually_exclusive_group(self, **kwargs), add_subparsers(self, **kwargs), convert_arg_line_to_args(self, arg_line), error(self, message), exit(self, status=0, message=None), format_help(self), format_usage(self), get_default(self, dest), parse_args(self, args=None, namespace=None), parse_intermixed_args(self, args=None, namespace=None), parse_known_args(self, args=None, namespace=None), parse_known_intermixed_args(self, args=None, namespace=None), print_help(self, file=None)
class ArgumentTypeError: add_note(), with_traceback()
class BooleanOptionalAction: format_usage(self)
class FileType
class HelpFormatter: add_argument(self, action), add_arguments(self, actions), add_text(self, text), add_usage(self, usage, actions, groups, prefix=None), end_section(self), format_help(self), start_section(self, heading)
class MetavarTypeHelpFormatter: add_argument(self, action), add_arguments(self, actions), add_text(self, text), add_usage(self, usage, actions, groups, prefix=None), end_section(self), format_help(self), start_section(self, heading)
class Namespace
class RawDescriptionHelpFormatter: add_argument(self, action), add_arguments(self, actions), add_text(self, text), add_usage(self, usage, actions, groups, prefix=None), end_section(self), format_help(self), start_section(self, heading)
class RawTextHelpFormatter: add_argument(self, action), add_arguments(self, actions), add_text(self, text), add_usage(self, usage, actions, groups, prefix=None), end_section(self), format_help(self), start_section(self, heading)
ngettext(msgid1, msgid2, n)

## circuitbreaker
class CircuitBreaker: EXPECTED_EXCEPTION(), call(self, func, *args, **kwargs), call_async(self, func, *args, **kwargs), call_async_generator(self, func, *args, **kwargs), call_generator(self, func, *args, **kwargs), closed (property), decorate(self, function), failure_count (property), fallback_function (property), last_failure (property), name (property), open_remaining (property), open_until (property), opened (property), reset(self)
class CircuitBreakerError: add_note(), with_traceback()
class CircuitBreakerMonitor: all_closed() -> bool, get(name: ~AnyStr) -> circuitbreaker.CircuitBreaker, get_circuits() -> Iterable[circuitbreaker.CircuitBreaker], get_closed() -> Iterable[circuitbreaker.CircuitBreaker], get_open() -> Iterable[circuitbreaker.CircuitBreaker], register(circuit_breaker)
build_failure_predicate(expected_exception)
ceil(x, /)
circuit(failure_threshold=None, recovery_timeout=None, expected_exception=None, name=None, fallback_function=None, cls=<class 'circuitbreaker.CircuitBreaker'>)
class datetime: astimezone(), combine(), ctime(), date(), dst(), fromisocalendar(), fromisoformat(), fromordinal(), fromtimestamp(), isocalendar(), isoformat(), isoweekday(), now(tz=None), replace(), strftime()
floor(x, /)
in_exception_list(*exc_types)
isasyncgenfunction(obj)
isclass(object)
iscoroutinefunction(func)
isgeneratorfunction(obj)
monotonic()
class timedelta: total_seconds()
class timezone: dst(), fromutc(), tzname(), utcoffset()
wraps(wrapped, assigned=('__module__', '__name__', '__qualname__', '__doc__', '__annotations__', '__type_params__'), updated=('__dict__',))

--- STRUCTURAL OVERVIEW (all selected files) ---
## fitz_sage/llm/providers/base.py
doc: "Provider protocols for LLM clients."
classes: RerankResult [@dataclass]; ChatProvider(Protocol) [chat -> str]; StreamingChatProvider(Protocol) [chat_stream -> Iterator[str]]; EmbeddingProvider(Protocol) [embed -> list[float], embed_batch -> list[list[float]], dimensions -> int]; RerankProvider(Protocol) [rerank -> list[RerankResult]]; VisionProvider(Protocol) [describe_image -> str]
imports: __future__, dataclasses, typing
exports: ModelTier, RerankResult, ChatProvider, StreamingChatProvider, EmbeddingProvider, RerankProvider, VisionProvider

## fitz_sage/llm/providers/openai.py
doc: "OpenAI provider wrappers using the official SDK."
classes: OpenAIChat [__init__ -> None, chat -> str, chat_stream -> Iterator[str]]; OpenAIEmbedding [__init__ -> None, embed -> list[float], embed_batch -> list[list[float]], dimensions -> int]; OpenAIVision [__init__ -> None, describe_image -> str]
imports: __future__, fitz_sage.llm.auth, fitz_sage.llm.auth.httpx_auth, fitz_sage.llm.providers.base, httpx, logging, openai, typing
exports: OpenAIChat, OpenAIEmbedding, OpenAIVision, CHAT_MODELS, EMBEDDING_MODEL, VISION_MODEL

## fitz_sage/llm/providers/anthropic.py
doc: "Anthropic provider wrappers using the official SDK."
classes: AnthropicChat [__init__ -> None, chat -> str, chat_stream -> Iterator[str]]; AnthropicVision [__init__ -> None, describe_image -> str]
functions: _extract_system_message(messages) -> tuple[str | None, list[dict[str, Any]]]
imports: __future__, anthropic, fitz_sage.llm.auth, fitz_sage.llm.auth.httpx_auth, fitz_sage.llm.providers.base, httpx, logging, typing
exports: AnthropicChat, AnthropicVision, CHAT_MODELS, VISION_MODEL

## fitz_sage/llm/providers/cohere.py
doc: "Cohere provider wrappers using the official SDK."
classes: CohereChat [__init__ -> None, chat -> str, chat_stream -> Iterator[str]]; CohereEmbedding [__init__ -> None, embed -> list[float], embed_batch -> list[list[float]], _embed_single_batch -> list[list[float]], dimensions -> int]; CohereRerank [__init__ -> None, rerank -> list[RerankResult]]
imports: __future__, cohere, fitz_sage.llm.auth, fitz_sage.llm.auth.httpx_auth, fitz_sage.llm.providers.base, httpx, logging, typing
exports: CohereChat, CohereEmbedding, CohereRerank, CHAT_MODELS, EMBEDDING_MODEL, RERANK_MODEL

## fitz_sage/llm/providers/ollama.py
doc: "Ollama provider wrappers using direct HTTP calls."
classes: OllamaChat [__init__ -> None, chat -> str, chat_stream -> Iterator[str], __del__ -> None]; OllamaEmbedding [__init__ -> None, _resolve_prefixes -> dict[str, str], _apply_prefix -> str, embed -> list[float], embed_batch -> list[list[float]], dimensions -> int, __del__ -> None]; OllamaRerank [__init__ -> None, rerank -> list[RerankResult], _score_document -> float, _parse_score -> float, __del__ -> None]; OllamaVision [__init__ -> None, describe_image -> str, __del__ -> None]
functions: _check_ollama_response(response, model) -> None
imports: __future__, fitz_sage.llm.providers.base, httpx, json, logging, re, typing
exports: OllamaChat, OllamaEmbedding, OllamaRerank, OllamaVision, CHAT_MODELS, EMBEDDING_MODEL, RERANK_MODEL, VISION_MODEL, DEFAULT_BASE_URL

## fitz_sage/core/instrumentation.py
doc: "Instrumentation system for benchmarking plugin performance."
classes: BenchmarkHook(Protocol) [on_call_start -> Any, on_call_end -> None]; CachingHook(Protocol) [get_cached_result -> Any, cache_result -> None]; InstrumentedProxy [__init__, __getattr__ -> Any, _wrap_method -> Callable, __repr__ -> str, __str__ -> str]
functions: register_hook(hook) -> None, unregister_hook(hook) -> None, clear_hooks() -> None, has_hooks() -> bool, get_hooks() -> list[BenchmarkHook], maybe_wrap(target, layer, plugin_name, methods_to_track) -> Any, wrap(target, layer, plugin_name, methods_to_track) -> InstrumentedProxy
imports: __future__, functools, logging, threading, typing
exports: BenchmarkHook, CachingHook, _NO_CACHE, register_hook, unregister_hook, clear_hooks, has_hooks, get_hooks, InstrumentedProxy, maybe_wrap, wrap

## fitz_sage/logging/logger.py
doc: "Unified logging setup for the entire Fitz project."
functions: configure_logging(level, fmt, stream), get_logger(name) -> StructuredLogger
imports: fitz_sage.utils.logging, logging, sys
exports: configure_logging, get_logger, set_query_context, clear_query_context, StructuredLogger

## fitz_sage/logging/tags.py
doc: "Central place for defining logging subsystem tags."

## fitz_sage/engines/fitz_krag/engine.py
doc: "FitzKragEngine - Knowledge Routing Augmented Generation engine."
classes: FitzKragEngine [__init__, load -> None, _wire_agentic_strategy -> None, _try_load_persisted_manifest -> None, _init_components -> None, _needs_detection -> bool, _build_detection_summary -> Any, _fast_analyze -> 'QueryAnalysis | None', answer -> Answer, _build_conflict_context -> dict | None, _build_gap_context -> dict, _check_cloud_cache -> Answer | None, _store_cloud_cache -> None, _build_cache_versions -> Any, point -> Any]
functions: _report_timings(progress, timings, pipeline_start) -> None
imports: __future__, concurrent.futures, fitz_sage, fitz_sage.cloud.cache_key, fitz_sage.cloud.client, fitz_sage.cloud.config, fitz_sage.core, fitz_sage.core.answer_mode, fitz_sage.core.paths, fitz_sage.engines.fitz_krag.config.schema, fitz_sage.engines.fitz_krag.context.assembler, fitz_sage.engines.fitz_krag.context.compressor, fitz_sage.engines.fitz_krag.generation.synthesizer, fitz_sage.engines.fitz_krag.ingestion.import_graph_store, fitz_sage.engines.fitz_krag.ingestion.raw_file_store, fitz_sage.engines.fitz_krag.ingestion.schema, fitz_sage.engines.fitz_krag.ingestion.section_store, fitz_sage.engines.fitz_krag.ingestion.symbol_store, fitz_sage.engines.fitz_krag.ingestion.table_store, fitz_sage.engines.fitz_krag.progressive.builder, fitz_sage.engines.fitz_krag.progressive.manifest, fitz_sage.engines.fitz_krag.query_analyzer, fitz_sage.engines.fitz_krag.query_batcher, fitz_sage.engines.fitz_krag.retrieval.expander, fitz_sage.engines.fitz_krag.retrieval.multihop, fitz_sage.engines.fitz_krag.retrieval.reader, fitz_sage.engines.fitz_krag.retrieval.reranker, fitz_sage.engines.fitz_krag.retrieval.router, fitz_sage.engines.fitz_krag.retrieval.strategies.agentic_search, fitz_sage.engines.fitz_krag.retrieval.strategies.code_search, fitz_sage.engines.fitz_krag.retrieval.strategies.llm_code_search, fitz_sage.engines.fitz_krag.retrieval.strategies.section_search, fitz_sage.engines.fitz_krag.retrieval.strategies.table_search, fitz_sage.engines.fitz_krag.retrieval.table_handler, fitz_sage.engines.fitz_krag.retrieval_profile, fitz_sage.governance, fitz_sage.governance.constraints.feature_extractor, fitz_sage.governance.decider, fitz_sage.llm.client, fitz_sage.llm.factory, fitz_sage.logging, fitz_sage.logging.logger, fitz_sage.retrieval.detection.modules, fitz_sage.retrieval.detection.protocol, fitz_sage.retrieval.detection.registry, fitz_sage.retrieval.entity_graph.store, fitz_sage.retrieval.hyde.generator, fitz_sage.retrieval.rewriter.rewriter, fitz_sage.retrieval.vocabulary.matcher, fitz_sage.retrieval.vocabulary.store, fitz_sage.storage.postgres, fitz_sage.tabular.store.postgres, pathlib, re, threading, time, typing, uuid

## fitz_sage/sdk/fitz.py
doc: "Fitz class - Stateful SDK for the Fitz KRAG framework."
classes: fitz [__init__ -> None, collection -> str, config_path -> Path, query -> Answer, _ensure_config -> None]
imports: __future__, fitz_sage.core, fitz_sage.core.firstrun, fitz_sage.core.paths, fitz_sage.logging.logger, fitz_sage.retrieval.rewriter.types, fitz_sage.services, pathlib, typing

## fitz_sage/api/routes/query.py
doc: "Query and chat endpoints."
functions: _to_conversation_context(history) -> ConversationContext | None, query(request) -> QueryResponse, chat(request) -> ChatResponse
imports: __future__, fastapi, fitz_sage.api.dependencies, fitz_sage.api.error_handlers, fitz_sage.api.models.schemas, fitz_sage.retrieval.rewriter.types

## fitz_sage/engines/fitz_krag/ingestion/pipeline.py
doc: "KRAG Ingestion Pipeline."
classes: KragIngestPipeline [__init__, ingest -> dict[str, Any], _scan_files -> list[Path], _relative_path -> str, _process_code_file -> tuple[list[SymbolEntry], list[dict[str, Any]]] | None, _process_doc_file -> list[SectionEntry] | None, _parse_document -> Any, _inject_vision_client -> None, _summarize_symbols -> list[str], _build_summary_prompt -> str, _parse_summary_response -> list[str], _summarize_sections -> list[str], _build_section_summary_prompt -> str, _process_table_file -> dict[str, Any] | None, _summarize_tables -> list[str], _build_table_summary_prompt -> str, _embed_summaries -> list[list[float]], _save_keywords_to_vocabulary -> None, _populate_entity_graph -> None, _generate_hierarchy_symbols -> None, _generate_hierarchy_sections -> None, _generate_corpus_summary -> None]
functions: _resolve_section_parents(section_dicts, file_ids) -> None, _hash_file(path) -> str
imports: __future__, collections.abc, fitz_sage.engines.fitz_krag.config.schema, fitz_sage.engines.fitz_krag.ingestion.enricher, fitz_sage.engines.fitz_krag.ingestion.import_graph_store, fitz_sage.engines.fitz_krag.ingestion.raw_file_store, fitz_sage.engines.fitz_krag.ingestion.schema, fitz_sage.engines.fitz_krag.ingestion.section_store, fitz_sage.engines.fitz_krag.ingestion.strategies.base, fitz_sage.engines.fitz_krag.ingestion.strategies.go, fitz_sage.engines.fitz_krag.ingestion.strategies.java, fitz_sage.engines.fitz_krag.ingestion.strategies.python_code, fitz_sage.engines.fitz_krag.ingestion.strategies.technical_doc, fitz_sage.engines.fitz_krag.ingestion.strategies.typescript, fitz_sage.engines.fitz_krag.ingestion.symbol_store, fitz_sage.engines.fitz_krag.ingestion.table_store, fitz_sage.ingestion.parser.router, fitz_sage.ingestion.source.base, fitz_sage.llm.client, fitz_sage.llm.providers.base, fitz_sage.retrieval.vocabulary.models, fitz_sage.storage.postgres, fitz_sage.tabular.parser.csv_parser, fitz_sage.tabular.store.postgres, hashlib, json, logging, pathlib, typing, uuid

## fitz_sage/ingestion/diff/executor.py
doc: "Executor for incremental (diff) ingestion."
classes: VectorDBWriter(Protocol) [upsert -> None]; Embedder(Protocol) [embed -> List[float], embed_batch -> List[List[float]]]; IngestSummary [@dataclass] [duration_seconds -> float, __str__ -> str]; DiffIngestExecutor [__init__ -> None, _enricher_id -> Optional[str], run -> IngestSummary, _prepare_file_no_enrich -> Optional[Dict], _upsert_file -> None, _detect_vocabulary -> None, _build_sparse_index -> None, ingest_artifacts -> tuple[int, List[str]]]
functions: _hash_text(text) -> str, run_diff_ingest(source) -> IngestSummary
imports: __future__, dataclasses, datetime, fitz_sage.core.chunk, fitz_sage.ingestion.chunking.router, fitz_sage.ingestion.diff.differ, fitz_sage.ingestion.diff.scanner, fitz_sage.ingestion.enrichment.pipeline, fitz_sage.ingestion.hashing, fitz_sage.ingestion.parser.router, fitz_sage.ingestion.source.base, fitz_sage.ingestion.state.manager, fitz_sage.retrieval.sparse, fitz_sage.retrieval.vocabulary, fitz_sage.tabular, fitz_sage.tabular.store, fitz_sage.tabular.store.base, hashlib, logging, pathlib, time, typing
exports: VectorDBWriter, Embedder, IngestSummary, DiffIngestExecutor, run_diff_ingest

## fitz_sage/ingestion/enrichment/pipeline.py
doc: "Unified enrichment pipeline."
classes: EnrichmentPipeline [__init__, _init_chunk_enricher -> None, _init_hierarchy_enricher -> None, _init_vocabulary_store -> None, _init_entity_graph -> None, from_config -> 'EnrichmentPipeline', chunk_enrichment_enabled -> bool, hierarchy_enrichment_enabled -> bool, artifacts_enabled -> bool, analyze_project -> ProjectAnalysis, get_applicable_artifact_plugins -> List[ArtifactPluginInfo], generate_artifacts -> List[Artifact], generate_structural_artifacts -> List[Artifact], enrich -> EnrichmentResult, _save_keywords -> None, _populate_entity_graph -> None, _detect_keyword_category -> str]
imports: __future__, fitz_sage.core.chunk, fitz_sage.ingestion.enrichment.artifacts.analyzer, fitz_sage.ingestion.enrichment.artifacts.base, fitz_sage.ingestion.enrichment.artifacts.registry, fitz_sage.ingestion.enrichment.base, fitz_sage.ingestion.enrichment.bus, fitz_sage.ingestion.enrichment.config, fitz_sage.ingestion.enrichment.hierarchy.enricher, fitz_sage.ingestion.enrichment.models, fitz_sage.llm.factory, fitz_sage.retrieval.entity_graph, fitz_sage.retrieval.vocabulary, logging, pathlib, re, typing
exports: EnrichmentPipeline, EnrichmentResult

## tests/integration/cloud_fixtures.py
doc: "Cloud-specific pytest fixtures for E2E integration tests."
functions: get_cloud_env_vars() -> dict[str, str | None], cloud_env_configured() -> bool, check_cloud_reachable(base_url, timeout) -> bool, cloud_config() -> CloudConfig, cloud_org_id() -> str, cloud_client(cloud_config, cloud_org_id) -> Generator[CloudClient, None, None], unique_collection_name() -> str, cache_versions(unique_collection_name) -> CacheVersions, cloud_pipeline(cloud_config, cloud_org_id, unique_collection_name), test_queries() -> dict[str, dict]
imports: __future__, fitz_sage, fitz_sage.cloud, fitz_sage.cloud.cache_key, fitz_sage.engines.fitz_krag.config, fitz_sage.engines.fitz_krag.engine, fitz_sage.ingestion.chunking.config, fitz_sage.ingestion.chunking.router, fitz_sage.ingestion.diff, fitz_sage.ingestion.parser, fitz_sage.ingestion.state, fitz_sage.llm, fitz_sage.vector_db.registry, httpx, os, pathlib, pytest, typing, uuid

## tools/governance/eval_pipeline.py
doc: "Full pipeline eval for governance classifier."
classes: GovernanceClassifier [__init__, _build_row -> pd.DataFrame, predict -> str, _predict_calibrated -> str, _predict_raw -> str]
functions: load_cases(data_dir) -> list[dict[str, Any]], case_to_chunks(case) -> list[Chunk], cosine_similarity(vec1, vec2) -> float, enrich_chunks_with_embeddings(query, chunks, embedder) -> None, make_constraints(chat, chat_balanced, embedder) -> list, run_constraints_individually(query, chunks, constraints) -> dict[str, ConstraintResult], fill_defaults(features) -> dict[str, Any], process_case(case, chat, embedder, detection_orchestrator, classifier, chat_balanced) -> dict[str, Any] | None, _collapse_3class(label) -> str, print_evaluation(rows, twostage) -> None, main()
imports: __future__, argparse, collections, concurrent.futures, csv, fitz_sage.config, fitz_sage.core.chunk, fitz_sage.governance, fitz_sage.governance.constraints.base, fitz_sage.governance.constraints.feature_extractor, fitz_sage.governance.constraints.plugins.answer_verification, fitz_sage.governance.constraints.plugins.causal_attribution, fitz_sage.governance.constraints.plugins.conflict_aware, fitz_sage.governance.constraints.plugins.insufficient_evidence, fitz_sage.governance.constraints.plugins.specific_info_type, fitz_sage.llm, fitz_sage.retrieval.detection.registry, joblib, json, math, numpy, pandas, pathlib, sys, threading, time, tqdm, typing

## tools/governance/extract_features.py
doc: "Feature extraction for governance classifier training."
functions: load_cases(data_dir) -> list[dict[str, Any]], case_to_chunks(case) -> list[Chunk], _cosine_similarity(vec1, vec2) -> float, enrich_chunks_with_embeddings(query, chunks, embedder) -> None, make_constraints(chat, chat_balanced, embedder) -> list, run_constraints_individually(query, chunks, constraints) -> dict[str, ConstraintResult], fill_defaults(features) -> dict[str, Any], get_governor_prediction(result_map) -> str, process_case(case, chat, chat_balanced, embedder, detection_orchestrator) -> dict[str, Any] | None, main()
imports: __future__, argparse, collections, concurrent.futures, csv, fitz_sage.config, fitz_sage.core.chunk, fitz_sage.governance, fitz_sage.governance.constraints.base, fitz_sage.governance.constraints.feature_extractor, fitz_sage.governance.constraints.plugins.answer_verification, fitz_sage.governance.constraints.plugins.causal_attribution, fitz_sage.governance.constraints.plugins.conflict_aware, fitz_sage.governance.constraints.plugins.insufficient_evidence, fitz_sage.governance.constraints.plugins.specific_info_type, fitz_sage.llm, fitz_sage.retrieval.detection.registry, json, math, pathlib, sqlite3, sys, threading, time, tqdm, typing

## fitz_sage/cli/commands/eval.py
doc: "Evaluation and observability commands."
functions: _get_collection(collection) -> str, _get_stats_pool(collection), governance_stats(collection, days, verbose, json_output) -> None, _display_stats(collection, days, distribution, constraints, flips, verbose) -> None, _display_rich(distribution, constraints, flips, verbose) -> None, _display_constraints_rich(constraints) -> None, _display_flips_rich(flips) -> None, _display_plain(distribution, constraints, flips, verbose) -> None, _get_engine(collection, engine_name), beir_benchmark(dataset, data_dir, collection, output) -> None, rgb_benchmark(collection, test_set, test_type, output) -> None, fitz_gov_benchmark(collection, category, data_dir, output, json_output, full, enrich, deterministic, fusion, adaptive, model) -> None, _display_fitz_gov_rich(result) -> None, _display_fitz_gov_plain(result) -> None, benchmark_dashboard(results_dir) -> None, run_all_benchmarks(collection, output_dir, beir_datasets, skip_beir) -> None, command() -> None
imports: __future__, fitz_sage.cli.context, fitz_sage.cli.ui, fitz_sage.config, fitz_sage.evaluation, fitz_sage.evaluation.benchmarks.beir, fitz_sage.evaluation.benchmarks.fitz_gov, fitz_sage.evaluation.benchmarks.rgb, fitz_sage.evaluation.dashboard, fitz_sage.logging.logger, fitz_sage.runtime, fitz_sage.storage.postgres, json, pathlib, typer, typing
exports: app, command

## fitz_sage/llm/auth/token_provider.py
doc: "Token provider adapter for OpenAI SDK azure_ad_token_provider pattern."
classes: TokenProviderAdapter [__init__ -> None, __call__ -> str]
imports: __future__, fitz_sage.llm.auth.base

## fitz_sage/llm/auth/base.py
doc: "Authentication provider protocol for LLM clients."
classes: AuthProvider(Protocol) [get_headers -> dict[str, str], get_request_kwargs -> dict[str, Any]]
imports: __future__, typing

## fitz_sage/llm/auth/certificates.py
doc: "Certificate validation utilities for startup-time verification."
classes: CertificateError(Exception)
functions: validate_certificate_file(path, cert_type) -> None, validate_key_file(path, key_type, password) -> None
imports: cryptography, cryptography.hazmat.primitives.serialization, datetime, logging, pathlib

## fitz_sage/llm/auth/m2m.py
doc: "M2M OAuth2 client credentials authentication provider."
classes: M2MAuth [__init__ -> None, _resolve_env_var -> str, _refresh_token -> None, _ensure_valid_token -> str, get_headers -> dict[str, str], get_request_kwargs -> dict[str, Any]]
imports: __future__, circuitbreaker, fitz_sage.llm.auth.certificates, httpx, logging, os, tenacity, threading, time, typing

## fitz_sage/llm/auth/api_key.py
doc: "API key authentication provider."
classes: ApiKeyAuth [__init__ -> None, api_key -> str, get_headers -> dict[str, str], get_request_kwargs -> dict[str, Any]]
imports: __future__, os, typing

## fitz_sage/llm/auth/composite.py
doc: "Composite auth for multi-header scenarios (BMW enterprise gateway)."
classes: CompositeAuth(AuthProvider) [__init__ -> None, get_headers -> dict[str, str], get_request_kwargs -> dict[str, Any]]
imports: __future__, fitz_sage.llm.auth.base, typing

## fitz_sage/core/paths/workspace.py
doc: "Workspace path management - foundation for all other paths."
classes: WorkspaceManager [set_workspace -> None, reset -> None, workspace -> Path, ensure_workspace -> Path]
functions: workspace() -> Path, ensure_workspace() -> Path, set_workspace(path) -> None, reset() -> None
imports: __future__, pathlib, typing

## fitz_sage/core/answer.py
doc: "Answer - paradigm-agnostic answer representation. See docs/API_REFERENCE.md for examples."
classes: Answer [@dataclass] [__post_init__]
imports: dataclasses, fitz_sage.core.answer_mode, provenance, typing

## fitz_sage/core/constraints.py
doc: "Constraints - Query-time constraints for knowledge engines."
classes: Constraints [@dataclass] [__post_init__]
imports: dataclasses, typing

## fitz_sage/core/utils.py
doc: "Core utilities shared across the Fitz codebase."
functions: extract_path(data, path) -> Any, set_nested_path(data, path, value) -> None
imports: __future__, re, typing
exports: extract_path, set_nested_path

## fitz_sage/core/provenance.py
doc: "Provenance - Source attribution for answers."
classes: Provenance [@dataclass] [__post_init__]
imports: dataclasses, typing

## fitz_sage/core/chunk.py
doc: "Chunk - fundamental unit of knowledge. See docs/API_REFERENCE.md for examples."
classes: Chunk(BaseModel)
imports: __future__, pydantic, typing
exports: Chunk

## Instructions

Write a complete architectural analysis covering ALL of the following sections. For each section, use the resolved decisions above as your primary source. Do not discover new things -- organize what has already been decided.

CITATION RULES:
- Reference each decision by its ID when you use it (e.g., "Per [d3], the interface returns TypeName").
- Always name specific classes, methods, and files (e.g., "`ClassName.method()` in `module.py`" not "the class's method").

### Section 1: Context
- Project description (what is being built, based on the task and decisions)
- Key requirements (derived from the decisions and their constraints)
- Constraints (from upstream decisions and codebase structure)
- Existing files (referenced in decisions' evidence)
- Needed artifacts: trace the call chain from entry point to implementation. If there are intermediate layers between the public API and the core logic, each layer needs changes -- do not skip layers.
- Assumptions (any remaining uncertainty after decisions)

### Section 2: Architecture
- At least 2 approaches considered (the chosen pattern + at least 1 rejected alternative)
- Clear recommendation with reasoning (from the pattern decisions)
- Key tradeoffs
- Scope statement (1-2 sentences on effort level)

### Section 3: Design
- ADRs for 3-5 key decisions that someone might disagree with (from the resolved decisions, excluding the architecture choice itself)
- Components with interfaces: list every component that needs changes, not just new ones. For each, specify the new method signature mirroring existing parameters.
- Data model if applicable
- Integration points
- Artifacts (config files, schemas -- write the complete content)

### Section 4: Roadmap
- Implementation phases (ordered by decision dependencies)
- Each phase: objective, deliverables, verification command, effort estimate
- Critical path and parallel opportunities

### Section 5: Risk
- Technical risks (from decision constraints and evidence)
- Each risk: impact, likelihood, mitigation, contingency, affected phases

ACCURACY RULE: Every file path, method name, and return type you write MUST come from the resolved decisions' evidence above or the codebase context. Do not invent.

Write your complete analysis as flowing prose. Do not output JSON -- the extraction step will handle that.