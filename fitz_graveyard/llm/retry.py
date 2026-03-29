# fitz_graveyard/llm/retry.py
"""Retry logic for Ollama API calls with exponential backoff."""

import logging

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# --- Ollama retry parameters ---
_OLLAMA_MAX_ATTEMPTS = 3  # Total attempts before giving up
_OLLAMA_BACKOFF_MIN_SECONDS = 4  # Minimum exponential backoff delay
_OLLAMA_BACKOFF_MAX_SECONDS = 60  # Maximum exponential backoff delay

# --- LM Studio retry parameters ---
_LM_STUDIO_MAX_ATTEMPTS = 3
_LM_STUDIO_BACKOFF_MIN_SECONDS = 5
_LM_STUDIO_BACKOFF_MAX_SECONDS = 60

# --- llama.cpp retry parameters (more attempts, shorter waits for model swap latency) ---
_LLAMA_CPP_MAX_ATTEMPTS = 5
_LLAMA_CPP_BACKOFF_MIN_SECONDS = 2
_LLAMA_CPP_BACKOFF_MAX_SECONDS = 30

# HTTP status codes considered transient (safe to retry)
_RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}

# HTTP status codes retryable for LM Studio/llama.cpp (no 500 — handled separately)
_LM_STUDIO_RETRYABLE_STATUSES = {408, 429, 502, 503, 504}


def is_retryable(exception: BaseException) -> bool:
    """
    Returns True if the exception should be retried.

    Retryable conditions:
    - ConnectionError (server unavailable)
    - ResponseError with status in (408, 429, 500, 502, 503, 504)
      BUT NOT status 500 with "requires more system memory" (that's OOM, handled by fallback)
    """
    if isinstance(exception, ConnectionError):
        return True

    try:
        from ollama import ResponseError
    except ImportError:
        return False

    if isinstance(exception, ResponseError):
        status = exception.status_code
        # Retryable HTTP status codes (transient errors)
        retryable_statuses = _RETRYABLE_HTTP_STATUSES

        if status not in retryable_statuses:
            return False

        # Special case: 500 with OOM message should NOT be retried (fallback handles it)
        if status == 500:
            error_msg = str(exception).lower()
            if "requires more system memory" in error_msg:
                return False

        return True

    return False


# Tenacity retry decorator for Ollama API calls
ollama_retry = retry(
    stop=stop_after_attempt(_OLLAMA_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=_OLLAMA_BACKOFF_MIN_SECONDS, max=_OLLAMA_BACKOFF_MAX_SECONDS),
    retry=retry_if_exception(is_retryable),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


def is_lm_studio_retryable(exception: BaseException) -> bool:
    """
    Returns True if the LM Studio exception should be retried.

    Retryable conditions:
    - ConnectionError (server unavailable)
    - httpx transport errors (connection refused, timeout)
    - openai APIConnectionError / APITimeoutError (model loading, server restart)
    """
    if isinstance(exception, ConnectionError):
        return True

    # httpx transport-level errors
    try:
        import httpx
        if isinstance(exception, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
            return True
    except ImportError:
        pass

    # openai SDK errors
    try:
        from openai import APIConnectionError, APITimeoutError, APIStatusError
        if isinstance(exception, (APIConnectionError, APITimeoutError)):
            return True
        if isinstance(exception, APIStatusError):
            return exception.status_code in _LM_STUDIO_RETRYABLE_STATUSES
    except ImportError:
        pass

    return False


# Tenacity retry decorator for LM Studio API calls
lm_studio_retry = retry(
    stop=stop_after_attempt(_LM_STUDIO_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=_LM_STUDIO_BACKOFF_MIN_SECONDS, max=_LM_STUDIO_BACKOFF_MAX_SECONDS),
    retry=retry_if_exception(is_lm_studio_retryable),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


def is_llama_cpp_retryable(exception: BaseException) -> bool:
    """
    Returns True if the llama.cpp exception should be retried.

    Same as LM Studio retryable conditions, plus 503 (model loading/swapping)
    and RuntimeError from server crashes (auto-restart may have failed).
    """
    # Reuse LM Studio logic for shared error types
    if is_lm_studio_retryable(exception):
        return True

    # Additionally handle 500 from llama-server during model loading
    try:
        from openai import APIStatusError
        if isinstance(exception, APIStatusError) and exception.status_code == 500:
            return True
    except ImportError:
        pass

    # Server crash/restart failures
    if isinstance(exception, RuntimeError):
        msg = str(exception).lower()
        if "llama-server" in msg or "crashed" in msg or "exited" in msg:
            return True

    return False


# Tenacity retry decorator for llama.cpp API calls
# More attempts + shorter waits to handle model swap latency
llama_cpp_retry = retry(
    stop=stop_after_attempt(_LLAMA_CPP_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=_LLAMA_CPP_BACKOFF_MIN_SECONDS, max=_LLAMA_CPP_BACKOFF_MAX_SECONDS),
    retry=retry_if_exception(is_llama_cpp_retryable),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
