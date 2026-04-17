# tests/unit/test_openai_api_client.py
"""Unit tests for the shared OpenAIApiClient base class."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("openai")

from fitz_forge.llm.openai_api import (
    OpenAIApiClient,
    _callable_to_openai_tool,
    _strip_thinking,
)
from fitz_forge.llm.types import AgentMessage, AgentToolCall


def _make_client(**kwargs):
    defaults = dict(
        base_url="http://localhost:9999/v1",
        model="test-model",
        timeout=30,
    )
    defaults.update(kwargs)
    with patch("fitz_forge.llm.openai_api.AsyncOpenAI"):
        return OpenAIApiClient(**defaults)


def _make_completion(content=None, tool_calls=None, finish_reason="stop"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(call_id, name, arguments_dict):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments_dict)
    return tc


async def _async_iter(items):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


class TestStripThinking:
    def test_removes_closed_think_block(self):
        assert _strip_thinking("<think>musing</think>answer") == "answer"

    def test_handles_unclosed_think_block(self):
        # Generation ended mid-thought — keep prefix
        assert _strip_thinking("prefix<think>cut off") == "prefix"

    def test_noop_on_clean_text(self):
        assert _strip_thinking("hello") == "hello"


class TestCallableToOpenaiTool:
    def test_type_mapping(self):
        def fn(a: str, b: int, c: bool, d: float): ...

        schema = _callable_to_openai_tool(fn)
        props = schema["function"]["parameters"]["properties"]
        assert props["a"]["type"] == "string"
        assert props["b"]["type"] == "integer"
        assert props["c"]["type"] == "boolean"
        assert props["d"]["type"] == "number"

    def test_required_vs_optional(self):
        def fn(x: str, y: int = 5): ...

        schema = _callable_to_openai_tool(fn)
        required = schema["function"]["parameters"]["required"]
        assert "x" in required and "y" not in required


# ---------------------------------------------------------------------------
# Properties and defaults
# ---------------------------------------------------------------------------


class TestProperties:
    def test_context_size_default(self):
        client = _make_client(context_length=65536)
        assert client.context_size == 65536

    def test_model_attribute(self):
        client = _make_client(model="m")
        assert client.model == "m"


# ---------------------------------------------------------------------------
# Thinking suppression
# ---------------------------------------------------------------------------


class TestExtraBody:
    def test_default_disables_thinking(self):
        client = _make_client()
        assert client._extra_body() == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_disable_thinking_false_returns_none(self):
        client = _make_client(disable_thinking=False)
        assert client._extra_body() is None


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerate:
    @pytest.mark.asyncio
    async def test_accumulates_streamed_content(self):
        client = _make_client()

        chunks = []
        for text in ["Hello", ", ", "world"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunks.append(chunk)

        client._client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))

        result = await client.generate([{"role": "user", "content": "hi"}])
        assert result == "Hello, world"

    @pytest.mark.asyncio
    async def test_passes_extra_body_when_disable_thinking(self):
        client = _make_client()
        chunks: list = []
        client._client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))

        await client.generate([{"role": "user", "content": "hi"}])

        _, kwargs = client._client.chat.completions.create.call_args
        assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    @pytest.mark.asyncio
    async def test_omits_extra_body_when_thinking_enabled(self):
        client = _make_client(disable_thinking=False)
        chunks: list = []
        client._client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))

        await client.generate([{"role": "user", "content": "hi"}])

        _, kwargs = client._client.chat.completions.create.call_args
        assert "extra_body" not in kwargs


# ---------------------------------------------------------------------------
# generate_with_tools
# ---------------------------------------------------------------------------


class TestGenerateWithTools:
    @pytest.mark.asyncio
    async def test_returns_agent_message_with_tool_calls(self):
        client = _make_client()

        tc = _make_tool_call("call-1", "list_dir", {"path": "."})
        completion = _make_completion(content=None, tool_calls=[tc], finish_reason="tool_calls")
        client._client.chat.completions.create = AsyncMock(return_value=completion)

        def list_dir(path: str) -> str:
            """List directory."""
            return ""

        msg = await client.generate_with_tools(
            messages=[{"role": "user", "content": "ls"}],
            tools=[list_dir],
        )

        assert isinstance(msg, AgentMessage)
        assert msg.tool_calls and len(msg.tool_calls) == 1
        assert isinstance(msg.tool_calls[0], AgentToolCall)
        assert msg.tool_calls[0].name == "list_dir"
        assert msg.tool_calls[0].arguments == {"path": "."}
        # assistant_dict is a plain OpenAI-format dict
        assert msg.assistant_dict["role"] == "assistant"
        assert "tool_calls" in msg.assistant_dict


# ---------------------------------------------------------------------------
# Tool result + metrics + default health check
# ---------------------------------------------------------------------------


class TestMisc:
    def test_tool_result_message(self):
        client = _make_client()
        m = client.tool_result_message("call-abc", "ok")
        assert m == {"role": "tool", "tool_call_id": "call-abc", "content": "ok"}

    def test_drain_metrics(self):
        client = _make_client()
        client._call_metrics = [{"elapsed_s": 1.0, "output_chars": 5, "model": "x"}]
        assert len(client.drain_call_metrics()) == 1
        assert client.drain_call_metrics() == []

    @pytest.mark.asyncio
    async def test_default_health_check_true_on_200(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("fitz_forge.llm.openai_api.httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_http.return_value)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value.get = AsyncMock(return_value=mock_response)
            result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_default_health_check_false_on_exception(self):
        client = _make_client()

        with patch("fitz_forge.llm.openai_api.httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_http.return_value)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value.get = AsyncMock(side_effect=Exception("boom"))
            result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_default_ensure_model_is_noop(self):
        client = _make_client()
        # Should not raise
        assert await client.ensure_model("anything") is None


# ---------------------------------------------------------------------------
# Retry behaviour via unified openai_api_retry
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self):
        client = _make_client()

        chunks = []
        for text in ["ok"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunks.append(chunk)

        calls = 0

        async def side_effect(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("refused")
            return _async_iter(chunks)

        client._client.chat.completions.create = AsyncMock(side_effect=side_effect)
        result = await client.generate([{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_valueerror(self):
        client = _make_client()
        client._client.chat.completions.create = AsyncMock(side_effect=ValueError("bad"))
        with pytest.raises(ValueError, match="bad"):
            await client.generate([{"role": "user", "content": "hi"}])
        assert client._client.chat.completions.create.call_count == 1
