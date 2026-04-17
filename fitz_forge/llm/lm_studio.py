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

    # Context length for smart_model (agent retrieval needs large context
    # for the structural index, even when planning uses small context).
    _SMART_CONTEXT_LENGTH = 65536

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "local-model",
        fallback_model: str | None = None,
        timeout: int = 300,
        context_length: int = 32768,
        gpu_guard: "GPUTemperatureGuard | None" = None,
        fast_model: str | None = None,
        smart_model: str | None = None,
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
        self._fast_model = fast_model
        self._smart_model = smart_model
        self.fallback_model = fallback_model

    @property
    def fast_model(self) -> str:
        return self._fast_model or self.model

    @property
    def smart_model(self) -> str:
        return self._smart_model or self.model

    # ------------------------------------------------------------------
    # lms CLI lifecycle
    # ------------------------------------------------------------------

    async def ensure_model(
        self,
        model_name: str,
        context_size: int | None = None,
    ) -> None:
        """Ensure the requested model is loaded in LM Studio."""
        if await self.is_model_loaded():
            return
        await self._load_model_via_cli()

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

        # Accept any loaded model. The orchestrator handles switching
        # to the right model before each stage. Health check only loads
        # a model if NOTHING is loaded — never unloads/reloads.
        if await self.is_model_loaded():
            return True
        first_model = (
            self.smart_model
            if self._smart_model and self._smart_model != self.model
            else self.model
        )
        logger.info(f"No model loaded, auto-loading {first_model}")
        return await self._load_model_via_cli(first_model)

    async def get_loaded_model(self) -> str | None:
        """Return the identifier of the currently loaded model, or None."""
        lms = shutil.which("lms")
        if not lms:
            return None

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [lms, "ps"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout + result.stderr
            if "No models" in output:
                return None
            for line in output.splitlines():
                line = line.strip()
                if not line or line.startswith("IDENTIFIER") or line.startswith("-"):
                    continue
                return line.split()[0]
            return None
        except Exception:
            return None

    async def is_model_loaded(self) -> bool:
        """Check if any model is currently loaded (not just available)."""
        return await self.get_loaded_model() is not None

    async def _load_model_via_cli(self, model_name: str | None = None) -> bool:
        """Load a model via ``lms load``."""
        model_name = model_name or self.model
        ctx = self._context_length
        if self._smart_model and model_name == self._smart_model:
            ctx = max(ctx, self._SMART_CONTEXT_LENGTH)

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

    async def switch_model(self, model_name: str) -> bool:
        """Unload current model and load the specified one.

        Skips the switch if the target model is already loaded (avoids
        CUDA context destruction on WDDM consumer GPUs).
        """
        loaded = await self.get_loaded_model()
        if loaded and loaded == model_name:
            logger.info(f"Model {model_name} already loaded, skipping switch")
            return True
        logger.info(f"Switching model: {loaded} -> {model_name}")
        await self.unload_model()
        await asyncio.sleep(3)
        ok = await self._load_model_via_cli(model_name)
        if not ok:
            logger.warning("Model load failed, retrying after 10s cooldown...")
            await asyncio.sleep(10)
            ok = await self._load_model_via_cli(model_name)
        if not ok:
            raise RuntimeError(
                f"Failed to load model '{model_name}' after retry. "
                f"Try restarting LM Studio or reducing context_length."
            )
        return True

    async def unload_model(self) -> bool:
        """Unload the current model via ``lms unload`` to free VRAM."""
        lms = shutil.which("lms")
        if not lms:
            logger.warning("lms CLI not found in PATH — cannot unload model")
            return False

        logger.info(f"Running: {lms} unload --all")
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [lms, "unload", "--all"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                logger.info(f"Model unloaded successfully: {output}")
                return True
            logger.warning(f"lms unload failed (code {result.returncode}): {output}")
            return False
        except Exception as e:
            logger.warning(f"lms unload failed: {e}")
            return False

    async def reload_model(self) -> bool:
        """Reload the model after unloading."""
        return await self._load_model_via_cli()
