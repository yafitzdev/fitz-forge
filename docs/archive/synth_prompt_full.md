# Full Synthesis Prompt

**Total: 45393 chars (~11348 tokens)**

You are writing a comprehensive architectural plan. All the hard decisions have already been made and resolved below. Your job is to narrate these decisions into a coherent, complete plan.

TASK: Add query result streaming so answers are delivered token-by-token instead of waiting for the full response



## Resolved Decisions

The following decisions were made by analyzing the actual source code. Each decision includes evidence and constraints. DO NOT contradict these decisions -- they are based on ground truth from the codebase.

### Decision d1+d9+d12
**Decided:** The new streaming chat interface uses `Iterator[str]` as its return type, and it differs from the existing non-streaming `chat()` method by returning a generator of tokens (`str`) instead a single final response string.
**Evidence:**
  - fitz_sage/llm/providers/base.py: ChatProvider.chat(messages: list[dict[str, Any]], **kwargs: Any) -> str
  - fitz_sage/llm/providers/base.py: StreamingChatProvider.chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/core/utils.py: extract_path(data: Any, path: str, *, default: Any = None, strict: bool = True) -> Any
  - fitz_sage/logging/tags.py: CHAT = "[CHAT]"
  - fitz_sage/llm/providers/base.py: def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]: ...
**Constraints:**
  - existing method `chat()` must not be modified — its signature and return type are frozen for backward compatibility
  - new method `chat_stream()` must preserve identical parameter list (`messages`, `**kwargs`) to ensure API parity with `chat()`
  - downstream implementations of `StreamingChatProvider` must return an `Iterator[str]`, not a `list[str]`, `Generator[str, ...]`, or other iterable subtype unless it is compatible with `Iterator[str]` at runtime
  - new utility `stream_to_answer()` must accept `(iterator: Iterator[str], *, provenance: List[Provenance] = [], mode: Optional[AnswerMode] = None, metadata: Dict[str, Any] = {}) -> Iterator[Answer]` and yield only valid `Answer` instances with non-None `text`
  - new streaming API (e.g., generator yielding partial answers) must still satisfy `Answer.__post_init__()` constraints — i.e., each yielded answer’s `text` must not be None
  - No new logging tags may be added to `logging/tags.py` for streaming — only existing `CHAT` tag must be reused
  - Streaming-specific structured context (e.g., per-chunk token count) must be passed via `extra={...}` in logger calls, not via new tag constants
  - `Answer`-yielding methods must preserve the original `query()` signature and must not rely on logging tags for correctness — logging is side-channel only

### Decision d3
**Decided:** The current `answer()` method consumes the LLM response by calling a chat provider that returns a final string (via `Answer`), and it is cannot be directly refactorable to accept an iterator of tokens without breaking its return contract — but a new parallel method `chat_stream()` can be added to `FitzKragEngine` that delegates to a streaming-capable provider returning `Iterator[str]`, while preserving the original `answer()` method unchanged.
**Evidence:**
  - fitz_sage/engines/fitz_krag/engine.py: def answer(self, query: Query, *, progress: Callable[[str], None] | None = None) -> Answer
  - fitz_sage/engines/fitz_krag/engine.py: class FitzKragEngine has no chat() or chat_stream() methods defined in visible scope
  - (from constraints) 'new method chat_stream() must preserve identical parameter list (messages, **kwargs)' and '(from constraints) downstream implementations of StreamingChatProvider must return an Iterator[str]'
**Constraints:**
  - existing method answer() must not be modified — its signature and return type are frozen
  - new method chat_stream(messages, **kwargs) -> Iterator[str] must be added as a parallel method with identical parameter list to the (unshown but frozen) chat() method
  - any new StreamingChatProvider implementations must return Iterator[str], not list[str], Generator, or other subtype unless it is runtime-compatible with Iterator[str]
  - chat_stream() cannot depend on answer() internally — it must delegate directly to a streaming-aware LLM provider

### Decision d8
**Decided:** There is no error handling pattern implemented for streaming failures in `OpenAIChat.chat_stream()` or `OllamaChat.chat_stream()` — both methods are only declared (stubbed with `...`) and contain no implementation, let alone any error handling logic.
**Evidence:**
  - openai.py: OpenAIChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - ollama.py: OllamaChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
**Constraints:**
  - new streaming implementations must define their error handling pattern from scratch — no existing pattern can be extended or reused
  - downstream decisions must not assume any pre-existing exception handling in `chat_stream()` methods, because they are unimplemented stubs

### Decision d2+d7+d11
**Decided:** All three concrete provider classes (`OpenAIChat`, `AnthropicChat`, and `CohereChat`) implement `chat_stream()` with signature `(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]`. They all return an `Iterator[str]` (not a generator subtype or async iterator), satisfying the runtime requirement for `Iterator[str]`.
**Evidence:**
  - fitz_sage/llm/providers/openai.py: OpenAIChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/llm/providers/anthropic.py: AnthropicChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/llm/providers/cohere.py: CohereChat.chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]
  - fitz_sage/core/instrumentation.py: BenchmarkHook.on_call_start(layer: str, plugin_name: str, method: str, args: tuple, kwargs: dict) -> Any
  - fitz_sage/core/instrumentation.py: BenchmarkHook.on_call_end(context: Any, result: Any, error: Exception | None) -> None
  - fitz_sage/core/instrumentation.py: InstrumentedProxy._wrap_method(method: Callable, method_name: str) -> Callable
  - fitz_sage/core/instrumentation.py: class InstrumentedProxy: __slots__ = ("_target", "_layer", "_plugin_name", "_methods_to_track")
  - fitz_sage/core/instrumentation.py: class BenchmarkHook(Protocol) defines only on_call_start(...) and on_call_end(...)
  - fitz_sage/llm/providers/openai.py: class OpenAIChat defines def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]: ... # 14 lines
  - fitz_sage/core/instrumentation.py: class InstrumentedProxy has __slots__ and _wrap_method(...) stubbed with no visible yield/iterator handling
**Constraints:**
  - existing method chat() must not be modified — its signature and return type are frozen for backward compatibility
  - new method chat_stream() must preserve identical parameter list (messages, **kwargs) to ensure API parity with chat()
  - downstream implementations of StreamingChatProvider must return an Iterator[str], not a list[str], Generator[str, ...], or other iterable subtype unless it is compatible with Iterator[str] at runtime
  - new method chat_stream(messages, **kwargs) -> Iterator[str] must be added as a parallel method with identical parameter list to the (unshown but frozen) chat() method
  - chat_stream() cannot depend on answer() internally — it must delegate directly to a streaming-aware LLM provider
  - instrumentation hooks in core/instrumentation.py MUST be extended to support streaming: new hook methods (e.g., on_yield, on_iterator_end) or wrapping logic for iterators MUST be added before chat_stream() can be safely instrumented
  - new streaming implementations must define their error handling pattern from scratch — no existing pattern can be extended or reused
  - circuit breaker support is not present in current codebase and must be implemented separately if needed — it cannot be assumed to exist or work with generators/iterators

### Decision d5
**Decided:** {
  "decision_id": "d5",
  "decision": "The `/query` endpoint currently returns a `QueryResponse` model (i.e., a full, non-streaming JSON response), and its handler function `query()` is implemented as an async function that internally calls the existing synchronous `query(question, source=None, top_k=None, conversation_context=None) -> Answer` method — which cannot be adapted to stream tokens. A new `/query_stream` endpoint must be added that uses a parallel `query_stream(...)` method returning

### Decision d4+d6+d10
**Decided:** The current `fitz.query()` method returns `Answer` and does not support streaming; a new method `query_stream()` must be introduced with identical parameter list to `query()` but returning `Iterator[str]`. However, since the source code shows no evidence of an existing `chat()` or `chat_stream()` method in this class — only `query()` — and the constraint requires adding `chat_stream(messages, **kwargs) -> Iterator[str]` as a parallel to an *unshown* frozen `chat()`, it is implies that `fitz.query()` is the primary query interface and must be extended via a new parallel method named `query_stream()` (not `chat_stream()`), because no `chat()` or `chat_stream()` methods exist in this class. Therefore, the decision must be: introduce `query_stream()` as a parallel to `query()`, not `chat_stream()`.
**Evidence:**
  - fitz_sage/sdk/fitz.py: def query(self, question: str, source: Optional[Union[str, Path]] = None, top_k: Optional[int] = None, conversation_context: Optional['ConversationContext'] = None) -> Answer
  - fitz_sage/sdk/fitz.py: (no method named 'chat' or 'chat_stream' appears in class fitz)
  - fitz_sage/core/answer.py: Answer.text: str
  - fitz_sage/core/answer.py: Answer.provenance: List[Provenance] = field(default_factory=list)
  - fitz_sage/core/answer.py: def __post_init__(self): if self.text is None: raise ValueError(...)
  - fitz_sage/core/answer.py: Answer.__post_init__(self) -> None
  - fitz_sage/sdk/fitz.py: fitz.query(self, question: str, source: Optional[Union[str, Path]] = None, top_k: Optional[int] = None, conversation_context: Optional['ConversationContext'] = None) -> Answer
  - fitz_sage/core/answer.py: @dataclass class Answer(text: str, provenance: List[Provenance], mode: Optional['AnswerMode'], metadata: Dict[str, Any])
**Constraints:**
  - existing method query(question, source=None, top_k=None, conversation_context=None) -> Answer must not be modified — its signature and return type are frozen
  - new method query_stream(question: str, source: Optional[Union[str, Path]] = None, top_k: Optional[int] = None, conversation_context: Optional['ConversationContext'] = None) -> Iterator[str] must be added as a parallel method with identical parameter list to query()
  - any new StreamingQueryProvider implementations must return Iterator[str], not list[str], Generator, or other subtype unless it is runtime-compatible with Iterator[str]
  - query_stream() cannot depend on query() internally — it must delegate directly to a streaming-aware LLM provider
  - existing method `Answer.__init__()` must not be modified — its signature and validation behavior (e.g., rejecting `text=None`) must remain unchanged
  - new streaming methods must produce `Answer` instances with the same structure: `text: str`, `provenance: List[Provenance]`, `mode: Optional[AnswerMode]`, `metadata: Dict[str, Any]`
  - any new streaming API (e.g., generator yielding partial answers) must still satisfy `Answer.__post_init__()` constraints — i.e., each yielded answer’s `text` must not be None
  - new streaming method (e.g., query_stream) must have identical signature to existing query() — same parameter names and types — to preserve parallel API contract
  - new streaming method must yield instances of Answer with text being a non-None str (i.e., cumulative answer chunks), never raw tokens or tuples
  - Answer.__init__() and __post_init__() must not be modified — all yielded answers must pass validation without raising ValueError('Answer text cannot be None...')
  - each yielded Answer must have provenance, mode, metadata fields matching the Answer dataclass structure (provenance: List[Provenance], mode: Optional[AnswerMode], metadata: Dict[str, Any])


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

Write the Context, Architecture, and Design sections below. Roadmap and Risk will be written in a separate pass — do NOT include them here.

For each section, use the resolved decisions above as your primary source. Do not discover new things -- organize what has already been decided.

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

ACCURACY RULE: Every file path, method name, and return type you write MUST come from the resolved decisions' evidence above or the codebase context. Do not invent.

Write your analysis as flowing prose. Do not output JSON -- the extraction step will handle that. Do NOT write Roadmap or Risk sections.