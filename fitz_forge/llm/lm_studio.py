# fitz_forge/llm/lm_studio.py
"""LM Studio client — OpenAI-compatible base + lms CLI lifecycle."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import TYPE_CHECKING

import httpx

from .openai_api import OpenAIApiClient

if TYPE_CHECKING:
    from .gpu_monitor import GPUTemperatureGuard

logger = logging.getLogger(__name__)


class LMStudioClient(OpenAIApiClient):
    """Async LM Studio client using the OpenAI-compatible API.

    LM Studio exposes ``/v1/chat/completions`` at http://localhost:1234/v1
    and the ``lms`` CLI for loading/unloading models out-of-process.
    """

    # Minimum context window in tokens.  With split reasoning (auto-enabled
    # when context_length < 32K), each call fits in ~8K tokens.  The minimum
    # is set to allow split mode on 16K context models.
    _MIN_CONTEXT_TOKENS = 8_192

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "local-model",
        fallback_model: str | None = None,
        timeout: int = 300,
        context_length: int = 32768,
        gpu_guard: "GPUTemperatureGuard | None" = None,
        api_key: str | None = None,
        disable_thinking: bool = True,
    ):
        super().__init__(
            base_url=base_url,
            model=model,
            timeout=timeout,
            api_key=api_key or "lm-studio",
            disable_thinking=disable_thinking,
            gpu_guard=gpu_guard,
            context_length=context_length,
        )
        self.fallback_model = fallback_model

    # ------------------------------------------------------------------
    # lms CLI lifecycle
    # ------------------------------------------------------------------

    async def ensure_model(
        self,
        model_name: str,
        context_size: int | None = None,
    ) -> None:
        """Ensure the requested model is loaded in LM Studio.

        ``lms load`` is a no-op when the target model is already loaded,
        so we always invoke it and let the CLI decide.
        """
        await self._load_model_via_cli(model_name)

    async def health_check(self) -> bool:
        """Check LM Studio is reachable and load the configured model.

        Raises RuntimeError if the configured context window is below the
        minimum required by the planning pipeline.
        """
        if self._context_length < self._MIN_CONTEXT_TOKENS:
            raise RuntimeError(
                f"Context window too small: {self._context_length} tokens "
                f"(minimum {self._MIN_CONTEXT_TOKENS}). "
                f"Increase context_length in config."
            )

        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                response = await http.get(f"{self.base_url}/models")
            if response.status_code != 200:
                return False
        except Exception as e:
            logger.error(f"LM Studio health check failed: {e}")
            return False

        # ``lms load`` is idempotent — if the model is already loaded
        # the CLI returns success without restarting it.
        return await self._load_model_via_cli(self.model)

    async def _load_model_via_cli(self, model_name: str | None = None) -> bool:
        """Load a model via ``lms load``."""
        model_name = model_name or self.model
        ctx = self._context_length

        lms = shutil.which("lms")
        if not lms:
            logger.warning(
                "lms CLI not found — cannot auto-load model. Load it manually in LM Studio."
            )
            return False

        logger.info(f"Running: lms load {model_name} -y -c {ctx} --parallel 1")
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    lms,
                    "load",
                    model_name,
                    "-y",
                    "-c",
                    str(ctx),
                    "--parallel",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                logger.info(f"Model {model_name} loaded successfully")
                return True
            logger.error(f"lms load failed (code {result.returncode}): {result.stderr[:300]}")
            return False
        except subprocess.TimeoutExpired:
            logger.error("lms load timed out after 300s")
            return False
        except Exception as e:
            logger.error(f"lms load failed: {e}")
            return False
