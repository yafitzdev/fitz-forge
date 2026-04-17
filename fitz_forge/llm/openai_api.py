# fitz_forge/llm/openai_api.py
"""
Shared OpenAI-compatible API base class.

All three LLM providers (LM Studio, llama.cpp, Ollama) expose an
OpenAI-compatible ``/v1/chat/completions`` endpoint.  This module
centralises streaming generation, tool-call handling, monitoring, and
metric tracking so the provider-specific subclasses only need to
implement lifecycle hooks (subprocess/CLI management, health checks,
etc.) and override model-tier properties where they differ.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from typing import TYPE_CHECKING

import httpx

from .retry import openai_api_retry
from .types import AgentMessage, AgentToolCall

if TYPE_CHECKING:
    from .gpu_monitor import GPUTemperatureGuard
    from .memory import MemoryMonitor

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# Strip <think>...</think> blocks that some models emit even when thinking
# is disabled.  Applied once after accumulation so all downstream parsers
# receive clean text.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_thinking(text: str) -> str:
    """Remove <think>…</think> blocks from model output."""
    text = _THINK_RE.sub("", text)
    # Handle unclosed <think> (generation ended mid-thought)
    if "<think>" in text:
        text = (
            text.split("</think>")[-1].lstrip()
            if "</think>" in text
            else text.split("<think>")[0].rstrip()
        )
    return text


# Maps Python type annotations → JSON Schema types
_TYPE_MAP = {
    str: "string",
    int: "integer",
    bool: "boolean",
    float: "number",
}


def _callable_to_openai_tool(fn) -> dict:
    """
    Convert a Python callable to an OpenAI tool schema dict.

    Uses inspect.signature() for parameter info, type annotations for types,
    and the first line of the docstring as the description.
    """
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    description = doc.splitlines()[0] if doc else fn.__name__

    properties = {}
    required = []

    for name, param in sig.parameters.items():
        annotation = param.annotation
        json_type = _TYPE_MAP.get(annotation, "string")
        properties[name] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


class OpenAIApiClient:
    """
    Base class for OpenAI-compatible chat completion providers.

    Subclasses implement lifecycle hooks (server/CLI management) and
    may override the tier properties.  All streaming, tool-call
    parsing, GPU-guard integration, memory monitoring, and metric
    tracking lives here.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int = 300,
        fast_model: str | None = None,
        smart_model: str | None = None,
        api_key: str | None = None,
        disable_thinking: bool = True,
        gpu_guard: "GPUTemperatureGuard | None" = None,
        context_length: int = 32768,
    ) -> None:
        if AsyncOpenAI is None:
            raise ImportError(
                "openai package required. Install with: pip install fitz-forge[lm-studio]"
            )

        self.base_url = base_url
        self.model = model
        self._timeout = timeout
        self._fast_model = fast_model
        self._smart_model = smart_model
        self._disable_thinking = disable_thinking
        self._gpu_guard = gpu_guard
        self._context_length = context_length
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "openai-compat",
            timeout=timeout,
        )
        self._call_metrics: list[dict] = []

    # ------------------------------------------------------------------
    # Model tier properties (subclasses may override)
    # ------------------------------------------------------------------

    @property
    def context_size(self) -> int:
        """Configured context window size in tokens."""
        return self._context_length

    @property
    def fast_model(self) -> str:
        """Model name for fast/screening tasks."""
        return self._fast_model or self.model

    @property
    def mid_model(self) -> str:
        """Model name for mid-tier tasks."""
        return self.model

    @property
    def smart_model(self) -> str:
        """Model name for reasoning tasks."""
        return self._smart_model or self.model

    # ------------------------------------------------------------------
    # Lifecycle hooks (default no-ops; subclasses override)
    # ------------------------------------------------------------------

    async def ensure_model(
        self,
        model_name: str,
        context_size: int | None = None,
    ) -> None:
        """Ensure the given model is loaded/available. Default: no-op."""
        return None

    async def health_check(self) -> bool:
        """Default health check: GET /v1/models; True if 200."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                response = await http.get(f"{self.base_url}/models")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"health_check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Extra-body helper
    # ------------------------------------------------------------------

    def _extra_body(self) -> dict | None:
        """Build the extra_body dict for chat completions.

        When ``disable_thinking`` is True, injects the Qwen chat-template
        kwarg that suppresses thinking-mode output.  Providers that don't
        recognise the kwarg (e.g. vanilla vLLM) should be instantiated
        with ``disable_thinking=False``.
        """
        if self._disable_thinking:
            return {"chat_template_kwargs": {"enable_thinking": False}}
        return None

    # ------------------------------------------------------------------
    # Core streaming generation
    # ------------------------------------------------------------------

    @openai_api_retry
    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 16384,
    ) -> str:
        """Generate a streaming response via OpenAI-compatible chat API."""
        if self._gpu_guard:
            await self._gpu_guard.preflight()

        model = model or self.model
        logger.info(f"{type(self).__name__}.generate: model={model}, messages={len(messages)}")

        t0 = time.monotonic()
        accumulated: list[str] = []
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
        }
        extra = self._extra_body()
        if extra is not None:
            kwargs["extra_body"] = extra
        if temperature is not None:
            kwargs["temperature"] = temperature

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                accumulated.append(delta.content)
            if self._gpu_guard:
                await self._gpu_guard.maybe_throttle()

        result = _strip_thinking("".join(accumulated))
        elapsed = time.monotonic() - t0
        est_tokens = len(result) / 4
        tok_s = est_tokens / elapsed if elapsed > 0 else 0
        self._call_metrics.append(
            {"elapsed_s": elapsed, "output_chars": len(result), "model": model}
        )
        logger.info(
            f"{type(self).__name__}.generate: {len(result)} chars in {elapsed:.1f}s "
            f"(~{tok_s:.1f} tok/s)"
        )
        return result

    # ------------------------------------------------------------------
    # Tool-calling
    # ------------------------------------------------------------------

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list,
        model: str | None = None,
        tool_choice: str = "auto",
    ) -> AgentMessage:
        """Single non-streaming chat call with tool definitions."""
        if self._gpu_guard:
            await self._gpu_guard.preflight()

        model = model or self.model
        openai_tools = [_callable_to_openai_tool(fn) for fn in tools]
        logger.info(
            f"{type(self).__name__}.generate_with_tools: model={model}, "
            f"messages={len(messages)}, tools={len(openai_tools)}, "
            f"tool_choice={tool_choice}"
        )

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "tools": openai_tools,
            "tool_choice": tool_choice,
            "stream": False,
            "max_tokens": 16384,
        }
        extra = self._extra_body()
        if extra is not None:
            kwargs["extra_body"] = extra

        response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        msg = choice.message
        logger.info(
            f"{type(self).__name__}.generate_with_tools: "
            f"finish_reason={choice.finish_reason}, tool_calls={bool(msg.tool_calls)}"
        )

        tool_calls: list[AgentToolCall] | None = None
        assistant_tool_calls: list[dict] | None = None
        if msg.tool_calls:
            tool_calls = []
            assistant_tool_calls = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    AgentToolCall(
                        id=tc.id or "",
                        name=tc.function.name,
                        arguments=args,
                    )
                )
                assistant_tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )

        assistant_dict: dict = {"role": "assistant", "content": msg.content}
        if assistant_tool_calls:
            assistant_dict["tool_calls"] = assistant_tool_calls

        return AgentMessage(
            content=msg.content,
            tool_calls=tool_calls,
            assistant_dict=assistant_dict,
        )

    # ------------------------------------------------------------------
    # Wrappers
    # ------------------------------------------------------------------

    async def generate_with_fallback(self, messages: list[dict]) -> tuple[str, str]:
        """Generate without any OOM/fallback handling.

        Returns the primary-model result and the model name used.
        Subclasses that want their own fallback behaviour should
        override this method.
        """
        result = await self.generate(messages)
        return result, self.model

    async def generate_with_monitoring(
        self,
        messages: list[dict],
        monitor: "MemoryMonitor",
    ) -> tuple[str, str]:
        """Generate with a MemoryMonitor running in parallel.

        If the monitor trips first, cancels generation and raises
        ``MemoryError``; otherwise returns the generation result.
        """
        monitor_task = asyncio.create_task(monitor.start_monitoring())
        generation_task = asyncio.create_task(self.generate_with_fallback(messages))

        done, _pending = await asyncio.wait(
            {monitor_task, generation_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if monitor_task in done:
            generation_task.cancel()
            try:
                await generation_task
            except asyncio.CancelledError:
                pass

            threshold_exceeded = monitor_task.result()
            if threshold_exceeded:
                raise MemoryError(
                    f"Memory threshold exceeded ({monitor.threshold_percent}%) during generation"
                )
            result = await generation_task
            return result

        monitor.stop()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        return generation_task.result()

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def drain_call_metrics(self) -> list[dict]:
        """Return and clear accumulated call metrics."""
        metrics = self._call_metrics
        self._call_metrics = []
        return metrics

    def tool_result_message(self, tool_call_id: str, content: str) -> dict:
        """Build an OpenAI-format tool result message."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
