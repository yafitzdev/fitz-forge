# tests/unit/test_llm_client.py
"""Tests for OllamaClient (OpenAI-compatible passthrough subclass)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("openai")

from fitz_forge.llm.ollama import OllamaClient


def _make_client(**kwargs):
    defaults = dict(
        base_url="http://localhost:11434",
        model="qwen2.5-coder:30b",
    )
    defaults.update(kwargs)
    with patch("fitz_forge.llm.openai_api.AsyncOpenAI"):
        return OllamaClient(**defaults)


async def _async_iter(items):
    for item in items:
        yield item


def _make_stream_chunks(texts):
    chunks = []
    for text in texts:
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = text
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Base URL normalisation
# ---------------------------------------------------------------------------


class TestBaseUrl:
    def test_appends_v1_if_missing(self):
        client = _make_client(base_url="http://localhost:11434")
        assert client.base_url == "http://localhost:11434/v1"

    def test_keeps_v1_if_present(self):
        client = _make_client(base_url="http://localhost:11434/v1")
        assert client.base_url == "http://localhost:11434/v1"

    def test_strips_trailing_slash(self):
        client = _make_client(base_url="http://localhost:11434/")
        assert client.base_url == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# Default health check hits /v1/models
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
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
    async def test_returns_false_on_connection_error(self):
        client = _make_client()
        with patch("fitz_forge.llm.openai_api.httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_http.return_value)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value.get = AsyncMock(side_effect=ConnectionError("refused"))
            result = await client.health_check()
        assert result is False


# ---------------------------------------------------------------------------
# Streaming generate
# ---------------------------------------------------------------------------


class TestGenerate:
    @pytest.mark.asyncio
    async def test_streams_and_accumulates(self):
        client = _make_client()
        chunks = _make_stream_chunks(["Hello ", "world", "!"])
        client._client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))

        result = await client.generate([{"role": "user", "content": "Hi"}])
        assert result == "Hello world!"

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self):
        client = _make_client()
        chunks = _make_stream_chunks(["ok"])
        calls = 0

        async def side_effect(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("refused")
            return _async_iter(chunks)

        client._client.chat.completions.create = AsyncMock(side_effect=side_effect)

        result = await client.generate([{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert calls == 2


# ---------------------------------------------------------------------------
# ensure_model is a no-op on the default subclass
# ---------------------------------------------------------------------------


class TestEnsureModel:
    @pytest.mark.asyncio
    async def test_noop(self):
        client = _make_client()
        # Should not raise or touch the network
        assert await client.ensure_model("anything") is None


# ---------------------------------------------------------------------------
# generate_with_fallback returns (result, model)
# ---------------------------------------------------------------------------


class TestGenerateWithFallback:
    @pytest.mark.asyncio
    async def test_returns_primary_model(self):
        client = _make_client(model="primary")
        chunks = _make_stream_chunks(["yes"])
        client._client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))

        result, model = await client.generate_with_fallback([{"role": "user", "content": "hi"}])
        assert result == "yes"
        assert model == "primary"
