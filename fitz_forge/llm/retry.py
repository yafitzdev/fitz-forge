# fitz_forge/llm/retry.py
"""Unified retry logic for OpenAI-compatible LLM providers."""

import logging

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def is_openai_api_retryable(exception: BaseException) -> bool:
    """Return True if the exception should trigger a retry.

    Covers every transient failure mode that can happen on the three
    providers that share the OpenAI-compatible chat completion API
    (LM Studio, llama.cpp, Ollama):

    - ``ConnectionError``
    - httpx transport errors (``ConnectError``, ``ReadTimeout``,
      ``ConnectTimeout``) raised during TCP setup
    - openai SDK ``APIConnectionError`` / ``APITimeoutError``
    - openai SDK ``APIStatusError`` with a transient HTTP status
      (408, 429, 500, 502, 503, 504) — includes 503s from model
      loading/swapping and 500s from llama-server under load
    - ``RuntimeError`` whose message mentions
      ``llama-server``/``crashed``/``exited`` (subprocess crash-restart
      failures surfaced by ``LlamaCppClient._ensure_alive``)
    """
    if isinstance(exception, ConnectionError):
        return True

    if isinstance(exception, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return True

    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        if isinstance(exception, (APIConnectionError, APITimeoutError)):
            return True
        if isinstance(exception, APIStatusError):
            return exception.status_code in {408, 429, 500, 502, 503, 504}
    except ImportError:
        pass

    if isinstance(exception, RuntimeError):
        msg = str(exception).lower()
        if "llama-server" in msg or "crashed" in msg or "exited" in msg:
            return True

    return False


# Unified retry decorator for all OpenAI-compatible providers.
# 5 attempts (most permissive of the three legacy decorators) with
# exponential backoff sized to tolerate model-swap latency.
openai_api_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(is_openai_api_retryable),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
