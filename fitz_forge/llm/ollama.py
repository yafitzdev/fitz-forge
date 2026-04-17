# fitz_forge/llm/ollama.py
"""Ollama client using its OpenAI-compatible endpoint.

Ollama exposes an OpenAI-compatible API at ``<base>/v1``.  We use it
directly via the shared OpenAIApiClient base class — no ollama SDK
dependency required.  Model lifecycle on Ollama is effectively
nothing (the server runs externally and pulls models on demand).
"""

from __future__ import annotations

import logging

from .openai_api import OpenAIApiClient

logger = logging.getLogger(__name__)


class OllamaClient(OpenAIApiClient):
    """Async Ollama client over the OpenAI-compatible ``/v1`` endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        fallback_model: str | None = None,
        timeout: int = 300,
        context_length: int = 131072,
        disable_thinking: bool = True,
    ) -> None:
        # Accept base_url either with or without the ``/v1`` suffix; the
        # OpenAI endpoint lives at <base>/v1 on Ollama.
        v1_base = base_url.rstrip("/")
        if not v1_base.endswith("/v1"):
            v1_base = f"{v1_base}/v1"

        super().__init__(
            base_url=v1_base,
            model=model,
            timeout=timeout,
            api_key="ollama",
            disable_thinking=disable_thinking,
            gpu_guard=None,
            context_length=context_length,
        )
        self.fallback_model = fallback_model
