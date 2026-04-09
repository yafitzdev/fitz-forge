# fitz_forge/llm/generate.py
"""
Single entry point for all LLM generate() calls.

Every call site in the pipeline should use this function instead of
calling client.generate() directly. This gives us one place to:
- Cap max_tokens to remaining context budget (prevents truncation)
- Sanitize common LLM output artifacts
- Detect truncation and retry once
- Write provenance traces (when configured)
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFETY_MARGIN = 512

# --- Module-level tracing state ---
_trace_dir: Path | None = None
_call_counter: int = 0


def configure_tracing(trace_dir: Path | str | None = None) -> None:
    """Set the trace directory for provenance logging.

    Call at the start of a pipeline run. Pass None to disable.
    Resets the call counter.
    """
    global _trace_dir, _call_counter
    _trace_dir = Path(trace_dir) if trace_dir else None
    _call_counter = 0


def get_trace_dir() -> Path | None:
    """Return the current trace directory, or None if tracing is disabled."""
    return _trace_dir


# --- Token estimation ---


def _estimate_prompt_tokens(messages: list[dict]) -> int:
    """Approximate token count: 1 token ~ 4 chars.

    Conservative estimate — a 10% error is fine when the budget
    is 44K vs the old hardcoded 4K.
    """
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
    return total // 4


# --- Budget capping ---


def _compute_max_tokens(
    client: Any,
    messages: list[dict],
    requested: int | None,
) -> int:
    """Cap max_tokens to remaining context budget.

    Formula: min(requested, context_size - prompt_tokens - safety_margin)

    If no context_size is available on the client, falls back to
    the requested value (or 16384 default).
    """
    context_size = getattr(client, "context_size", 0)
    if not isinstance(context_size, (int, float)) or context_size <= 0:
        return requested or 16384

    prompt_tokens = _estimate_prompt_tokens(messages)
    budget = context_size - prompt_tokens - _SAFETY_MARGIN
    budget = max(budget, 256)  # floor — never go below 256

    if requested is None:
        return budget
    return min(requested, budget)


# --- Output sanitization ---


def _sanitize(text: str) -> str:
    """Clean common LLM output artifacts."""
    # Quadruple docstrings (model generates """" instead of """)
    text = text.replace('""""', '"""')
    # Unicode replacement characters (encoding artifacts)
    text = text.replace("\ufffd", "")
    return text


# --- Truncation detection ---


def _is_truncated(text: str) -> bool:
    """Detect common truncation patterns."""
    stripped = text.rstrip()
    if not stripped:
        return False

    # Unclosed code fence
    if stripped.count("```") % 2 != 0:
        return True

    # Significant bracket imbalance (2+ means real truncation)
    opens = stripped.count("{") + stripped.count("[")
    closes = stripped.count("}") + stripped.count("]")
    if opens > closes + 2:
        return True

    # For JSON-like output: unclosed string
    first = stripped.lstrip()[:1]
    if first in ("{", "["):
        if stripped.count('"') % 2 != 0:
            return True

    return False


# --- Main entry point ---


async def generate(
    client: Any,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    label: str | None = None,
) -> str:
    """Generate an LLM response with quality guardrails.

    Args:
        client: Any LLM client with a generate() method and context_size property.
        messages: Chat messages in OpenAI format.
        model: Model name override (passed through to client).
        temperature: Sampling temperature (passed through to client).
        max_tokens: Desired output cap. Will be clamped to remaining
            context budget if it exceeds it. None = use full budget.
        label: Human-readable name for provenance traces (e.g. "decomp_candidate_1").

    Returns:
        Sanitized response text.
    """
    global _call_counter

    effective_max = _compute_max_tokens(client, messages, max_tokens)

    kwargs: dict[str, Any] = {"messages": messages, "max_tokens": effective_max}
    if model is not None:
        kwargs["model"] = model
    if temperature is not None:
        kwargs["temperature"] = temperature

    if max_tokens is not None and effective_max < max_tokens:
        prompt_est = _estimate_prompt_tokens(messages)
        logger.info(
            "generate: capped max_tokens %d -> %d (context=%s, prompt~%d tok)",
            max_tokens,
            effective_max,
            getattr(client, "context_size", "?"),
            prompt_est,
        )

    t0 = time.monotonic()
    raw = await client.generate(**kwargs)
    elapsed = time.monotonic() - t0

    result = _sanitize(raw)

    # Truncation detection + 1 retry
    if _is_truncated(result):
        logger.warning(
            "generate: truncation detected (%d chars, %.1fs), retrying",
            len(result),
            elapsed,
        )
        t0_retry = time.monotonic()
        retry_raw = await client.generate(**kwargs)
        retry_elapsed = time.monotonic() - t0_retry
        retry_result = _sanitize(retry_raw)

        if not _is_truncated(retry_result) or len(retry_result) > len(result):
            result = retry_result
            elapsed = retry_elapsed
            logger.info(
                "generate: retry succeeded (%d chars, %.1fs)",
                len(result),
                elapsed,
            )
        else:
            logger.warning("generate: retry also truncated, keeping original")

    # Provenance tracing
    trace_dir = _trace_dir
    if trace_dir is not None:
        _call_counter += 1
        trace_dir.mkdir(parents=True, exist_ok=True)
        name = label or "generate"
        trace_file = trace_dir / f"{_call_counter:03d}_{name}.json"
        trace = {
            "call_number": _call_counter,
            "label": name,
            "messages": messages,
            "output": result,
            "max_tokens_requested": max_tokens,
            "max_tokens_effective": effective_max,
            "temperature": temperature,
            "elapsed_s": round(elapsed, 2),
            "output_chars": len(result),
        }
        try:
            trace_file.write_text(
                json.dumps(trace, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("generate: trace write failed: %s", e)

    return result
