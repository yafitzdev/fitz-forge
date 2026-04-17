# fitz_forge/llm/llama_cpp.py
"""
llama.cpp client with subprocess management and model tier support.

Manages a llama-server process as a subprocess. Restarts the server
when switching between fast/smart model tiers (since context_size and
gpu_layers are server-level settings).
"""

from __future__ import annotations

import asyncio
import atexit
import ctypes
import json
import logging
import platform
import statistics
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .openai_api import OpenAIApiClient, _strip_thinking
from .retry import openai_api_retry

if TYPE_CHECKING:
    from fitz_forge.config.schema import LlamaCppModelConfig

    from .gpu_monitor import GPUTemperatureGuard

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


class TokSecBaseline:
    """Tracks historical tok/s per model and detects performance degradation.

    Persists baselines to a JSON file alongside the config. When current
    tok/s drops below 50% of the median baseline, signals that a GPU
    driver reset is needed.
    """

    _DEGRADATION_RATIO = 0.5  # trigger reset if prefill tok/s < 50% of baseline
    _MIN_SAMPLES = 5  # need this many samples before checking for drift
    _MAX_SAMPLES = 50  # rolling window
    _MIN_PREFILL_S = 0.5  # ignore very fast calls (too noisy)

    def __init__(self, path: Path | None = None):
        if path is None:
            import platformdirs

            path = Path(platformdirs.user_config_path("fitz-forge")) / "tok_baselines.json"
        self._path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    @staticmethod
    def _key(model_path: str, context_size: int) -> str:
        return f"{model_path}::ctx{context_size}"

    def record(
        self, model_path: str, context_size: int, prefill_tok_s: float, prefill_s: float
    ) -> None:
        """Record a prefill tok/s sample. Ignores very fast calls (too noisy)."""
        if prefill_tok_s < 1.0 or prefill_s < self._MIN_PREFILL_S:
            return
        key = self._key(model_path, context_size)
        entry = self._data.setdefault(key, {"samples": []})
        entry["samples"].append(round(prefill_tok_s, 1))
        if len(entry["samples"]) > self._MAX_SAMPLES:
            entry["samples"] = entry["samples"][-self._MAX_SAMPLES :]
        entry["median"] = round(statistics.median(entry["samples"]), 1)
        self._save()

    def is_degraded(
        self, model_path: str, context_size: int, prefill_tok_s: float, prefill_s: float
    ) -> bool:
        """Check if current prefill tok/s indicates GPU performance degradation."""
        if prefill_tok_s < 1.0 or prefill_s < self._MIN_PREFILL_S:
            return False
        key = self._key(model_path, context_size)
        entry = self._data.get(key)
        if not entry or len(entry.get("samples", [])) < self._MIN_SAMPLES:
            return False
        median = entry.get("median", 0)
        if median <= 0:
            return False
        ratio = prefill_tok_s / median
        if ratio < self._DEGRADATION_RATIO:
            logger.warning(
                f"Performance degradation detected: {prefill_tok_s:.0f} prefill tok/s "
                f"vs baseline {median:.0f} prefill tok/s ({ratio:.0%})"
            )
            return True
        return False


class LlamaCppClient(OpenAIApiClient):
    """
    Async llama.cpp client that manages llama-server as a subprocess.

    Restarts the server when switching between fast/smart tiers, since
    context_size and gpu_layers are server-level (not per-request).
    """

    # Minimum context window in tokens.  See OpenAIApiClient.
    _MIN_CONTEXT_TOKENS = 32_768

    def __init__(
        self,
        server_path: str,
        models_dir: str,
        fast_model: "LlamaCppModelConfig",
        mid_model: "LlamaCppModelConfig | None" = None,
        smart_model: "LlamaCppModelConfig | None" = None,
        port: int = 8012,
        timeout: int = 300,
        startup_timeout: int = 120,
        gpu_guard: "GPUTemperatureGuard | None" = None,
        disable_thinking: bool = True,
    ):
        if AsyncOpenAI is None:
            raise ImportError(
                "openai package required for llama.cpp support. "
                "Install with: pip install openai"
            )

        super().__init__(
            base_url=f"http://127.0.0.1:{port}/v1",
            model=fast_model.path,
            timeout=timeout,
            fast_model=fast_model.path,
            smart_model=(smart_model.path if smart_model else fast_model.path),
            api_key="llama-cpp",
            disable_thinking=disable_thinking,
            gpu_guard=gpu_guard,
            context_length=fast_model.context_size,
        )

        self._server_path = server_path
        self._models_dir = models_dir
        self._fast_model_cfg = fast_model
        self._mid_model_cfg = mid_model or fast_model
        self._smart_model_cfg = smart_model or fast_model
        self._port = port
        self._startup_timeout = startup_timeout

        # Public attribute for interface parity with OllamaClient/LMStudioClient
        self.fallback_model = smart_model.path if smart_model else None

        self._process: subprocess.Popen | None = None
        # Base class already created an _client; replace it when the server
        # actually starts so timeout is respected and only active after start.
        self._client = None  # type: ignore[assignment]
        self._active_tier: str | None = None
        self._active_context_size: int | None = None
        self._baseline = TokSecBaseline()
        self._degradation_warned = False

    # ------------------------------------------------------------------
    # Model tier properties
    # ------------------------------------------------------------------

    @property
    def context_size(self) -> int:
        """Active context window size in tokens."""
        return self._active_context_size or self._fast_model_cfg.context_size

    @property
    def fast_model(self) -> str:
        """Model name for fast/screening tasks."""
        return self._fast_model_cfg.path

    @property
    def mid_model(self) -> str:
        """Model name for mid-tier/summarization tasks."""
        return self._mid_model_cfg.path

    @property
    def smart_model(self) -> str:
        """Model name for smart/reasoning tasks."""
        return self._smart_model_cfg.path

    @property
    def active_model(self) -> str:
        """Path of the model currently loaded in llama-server."""
        if self._active_tier == "smart":
            return self._smart_model_cfg.path
        if self._active_tier == "mid":
            return self._mid_model_cfg.path
        return self._fast_model_cfg.path

    # ------------------------------------------------------------------
    # Subprocess lifecycle
    # ------------------------------------------------------------------

    async def start(self, tier: str = "fast", context_size: int | None = None) -> None:
        """Start llama-server subprocess for the given tier."""
        if self._process is not None:
            await self.stop()

        tier_map = {
            "fast": self._fast_model_cfg,
            "mid": self._mid_model_cfg,
            "smart": self._smart_model_cfg,
        }
        model_cfg = tier_map.get(tier, self._fast_model_cfg)
        model_path = str(Path(self._models_dir) / model_cfg.path)
        ctx = context_size or model_cfg.context_size

        cmd = [
            self._server_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "-m",
            model_path,
            "-c",
            str(ctx),
            "-ngl",
            str(model_cfg.gpu_layers),
        ]
        if model_cfg.flash_attention:
            cmd.extend(["--flash-attn", "on"])
        if model_cfg.cache_type_k:
            cmd.extend(["--cache-type-k", model_cfg.cache_type_k])
        if model_cfg.cache_type_v:
            cmd.extend(["--cache-type-v", model_cfg.cache_type_v])

        logger.info(f"Starting llama-server ({tier}): {' '.join(cmd)}")

        self._kill_orphaned_servers()

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        proc = self._process
        atexit.register(lambda: proc.kill() if proc.poll() is None else None)

        await self._wait_for_ready()

        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key="llama-cpp",
            timeout=self._timeout,
        )
        self._active_tier = tier
        self._active_context_size = ctx
        self.model = model_cfg.path

        logger.info(f"llama-server ready ({tier}): {model_cfg.path} (ctx={ctx})")

    @staticmethod
    def _kill_orphaned_servers() -> None:
        """Kill any llama-server processes left over from previous runs."""
        if platform.system() != "Windows":
            return
        try:
            result = subprocess.run(
                ["taskkill", "/IM", "llama-server.exe", "/F"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info("Killed orphaned llama-server process(es)")
        except Exception:
            pass

    async def stop(self) -> None:
        """Stop the llama-server subprocess."""
        if self._process is None:
            return

        logger.info("Stopping llama-server...")
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("llama-server did not stop, killing")
            self._process.kill()
            self._process.wait(timeout=5)

        self._process = None
        self._active_tier = None
        self._client = None  # type: ignore[assignment]
        logger.info("llama-server stopped")

    async def ensure_model(
        self,
        model_name: str,
        context_size: int | None = None,
    ) -> None:
        """Switch to the tier for the given model name."""
        await self._ensure_tier(model_name, context_size=context_size)

    async def _ensure_tier(
        self,
        model_name: str | None,
        context_size: int | None = None,
    ) -> None:
        """Restart server only if the actual model path or context size differs."""
        if model_name is None:
            if self._active_tier is None:
                await self.start("fast", context_size=context_size)
            return

        if model_name == self._smart_model_cfg.path:
            needed = "smart"
        elif (
            model_name == self._mid_model_cfg.path
            and self._mid_model_cfg.path != self._fast_model_cfg.path
        ):
            needed = "mid"
        else:
            needed = "fast"

        tier_map = {
            "fast": self._fast_model_cfg,
            "mid": self._mid_model_cfg,
            "smart": self._smart_model_cfg,
        }
        needed_cfg = tier_map[needed]
        active_cfg = tier_map.get(self._active_tier) if self._active_tier else None

        same_model = (
            active_cfg is not None
            and active_cfg.path == needed_cfg.path
            and active_cfg.gpu_layers == needed_cfg.gpu_layers
        )
        ctx_changed = (
            context_size is not None
            and self._active_context_size is not None
            and context_size != self._active_context_size
        )

        if same_model and not ctx_changed:
            if self._active_tier != needed:
                logger.debug(f"Tier {self._active_tier}→{needed} (same model, skipping restart)")
                self._active_tier = needed
            return

        reason = f"tier {self._active_tier}→{needed}"
        if ctx_changed:
            reason += f", ctx {self._active_context_size}→{context_size}"
        logger.info(f"Restarting llama-server: {reason} (model={model_name})")
        await self.stop()
        await self.start(needed, context_size=context_size)

    @staticmethod
    def _reset_gpu_driver() -> bool:
        """Simulate Ctrl+Win+Shift+B to reset the GPU display driver."""
        if platform.system() != "Windows":
            return False

        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002

            VK_LWIN = 0x5B
            VK_CONTROL = 0x11
            VK_SHIFT = 0x10
            VK_B = 0x42

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", ctypes.c_long),
                    ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.c_ushort),
                    ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class HARDWAREINPUT(ctypes.Structure):
                _fields_ = [
                    ("uMsg", ctypes.c_ulong),
                    ("wParamL", ctypes.c_ushort),
                    ("wParamH", ctypes.c_ushort),
                ]

            class INPUT(ctypes.Structure):
                class _INPUT(ctypes.Union):
                    _fields_ = [
                        ("ki", KEYBDINPUT),
                        ("mi", MOUSEINPUT),
                        ("hi", HARDWAREINPUT),
                    ]

                _fields_ = [
                    ("type", ctypes.c_ulong),
                    ("ii", _INPUT),
                ]

            def _key_input(vk: int, flags: int = 0) -> INPUT:
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.ii.ki.wVk = vk
                inp.ii.ki.dwFlags = flags
                return inp

            inputs = (INPUT * 8)(
                _key_input(VK_CONTROL),
                _key_input(VK_LWIN),
                _key_input(VK_SHIFT),
                _key_input(VK_B),
                _key_input(VK_B, KEYEVENTF_KEYUP),
                _key_input(VK_SHIFT, KEYEVENTF_KEYUP),
                _key_input(VK_LWIN, KEYEVENTF_KEYUP),
                _key_input(VK_CONTROL, KEYEVENTF_KEYUP),
            )

            sent = user32.SendInput(8, ctypes.pointer(inputs[0]), ctypes.sizeof(INPUT))
            if sent != 8:
                logger.warning(f"GPU driver reset: SendInput returned {sent}/8")
                return False

            logger.info("GPU driver reset: Ctrl+Win+Shift+B sent successfully")
            return True
        except Exception as e:
            logger.warning(f"GPU driver reset failed: {e}")
            return False

    async def _wait_for_ready(self) -> None:
        """Poll /health until server responds 200 or timeout."""
        url = f"http://127.0.0.1:{self._port}/health"
        deadline = time.monotonic() + self._startup_timeout

        async with httpx.AsyncClient(timeout=2.0) as http:
            while time.monotonic() < deadline:
                if self._process and self._process.poll() is not None:
                    stderr = ""
                    if self._process.stderr:
                        stderr = self._process.stderr.read().decode(errors="replace")
                    raise RuntimeError(
                        f"llama-server exited with code {self._process.returncode}: {stderr[:500]}"
                    )
                try:
                    resp = await http.get(url)
                    if resp.status_code == 200:
                        return
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                await asyncio.sleep(1.0)

        raise TimeoutError(f"llama-server did not become ready within {self._startup_timeout}s")

    async def _ensure_alive(self) -> None:
        """Restart the server if it has crashed, raise if not started."""
        if self._process is None:
            raise RuntimeError("llama-server not started")
        if self._process.poll() is not None:
            stderr = ""
            if self._process.stderr:
                stderr = self._process.stderr.read().decode(errors="replace")
            code = self._process.returncode
            logger.warning(f"llama-server crashed (code {code}), restarting: {stderr[:500]}")
            self._process = None
            self._client = None  # type: ignore[assignment]
            tier = self._active_tier or "fast"
            self._active_tier = None

            if self._reset_gpu_driver():
                await asyncio.sleep(3)

            await self.start(tier)

    async def _auto_reset_gpu(self) -> None:
        """Stop llama-server, reset GPU driver, restart."""
        tier = self._active_tier or "fast"
        ctx = self._active_context_size

        logger.info("Auto-reset: stopping llama-server for GPU driver reset")
        await self.stop()

        if self._reset_gpu_driver():
            await asyncio.sleep(5)
            logger.info("Auto-reset: GPU driver reset complete, restarting server")
        else:
            logger.warning("Auto-reset: GPU driver reset failed, restarting anyway")
            await asyncio.sleep(1)

        await self.start(tier, context_size=ctx)
        self._degradation_warned = False

    # ------------------------------------------------------------------
    # Interface methods
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if llama-server is running, healthy, and context is sufficient."""
        ctx = self._active_context_size or self._fast_model_cfg.context_size
        if ctx < self._MIN_CONTEXT_TOKENS:
            raise RuntimeError(
                f"Context window too small: {ctx} tokens "
                f"(minimum {self._MIN_CONTEXT_TOKENS}). "
                f"Increase context_size in llama_cpp config."
            )
        try:
            await self._ensure_alive()
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(f"http://127.0.0.1:{self._port}/health")
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"llama-cpp health check failed: {e}")
            return False

    @openai_api_retry
    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 16384,
    ) -> str:
        """Generate a streaming response. Switches tier if model differs.

        Overrides the base implementation to additionally capture prefill
        timing for the GPU degradation detector.
        """
        if self._gpu_guard:
            await self._gpu_guard.preflight()

        await self._ensure_tier(model)
        await self._ensure_alive()

        effective_model = model or self.model
        logger.info(f"LlamaCpp.generate: model={effective_model}, messages={len(messages)}")

        t0 = time.monotonic()
        t_first_token = None
        accumulated: list[str] = []
        kwargs: dict = {
            "model": effective_model,
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
                if t_first_token is None:
                    t_first_token = time.monotonic()
                accumulated.append(delta.content)
            if self._gpu_guard:
                await self._gpu_guard.maybe_throttle()

        result = _strip_thinking("".join(accumulated))
        t_end = time.monotonic()
        elapsed = t_end - t0
        prefill_s = (t_first_token - t0) if t_first_token else elapsed
        gen_s = (t_end - t_first_token) if t_first_token else 0.0
        est_output_tokens = len(result) / 4
        est_input_tokens = sum(len(m.get("content", "")) for m in messages) / 4
        gen_tok_s = est_output_tokens / gen_s if gen_s > 0.05 else 0.0
        prefill_tok_s = est_input_tokens / prefill_s if prefill_s > 0.05 else 0.0
        self._call_metrics.append(
            {
                "elapsed_s": elapsed,
                "prefill_s": prefill_s,
                "gen_s": gen_s,
                "output_chars": len(result),
                "tok_s": gen_tok_s,
                "prefill_tok_s": prefill_tok_s,
                "model": effective_model,
            }
        )
        logger.info(
            f"LlamaCpp.generate: {len(result)} chars in {elapsed:.1f}s "
            f"(prefill {prefill_s:.1f}s ~{prefill_tok_s:.0f} tok/s, "
            f"gen ~{gen_tok_s:.1f} tok/s)"
        )

        ctx = self._active_context_size or 0
        if ctx > 0:
            degraded = not self._degradation_warned and self._baseline.is_degraded(
                effective_model,
                ctx,
                prefill_tok_s,
                prefill_s,
            )
            if degraded:
                self._degradation_warned = True
                logger.warning("GPU degradation detected — auto-resetting GPU driver")
                await self._auto_reset_gpu()
            else:
                self._baseline.record(effective_model, ctx, prefill_tok_s, prefill_s)

        return result

    async def generate_with_fallback(
        self,
        messages: list[dict],
    ) -> tuple[str, str]:
        """Generate using the smart model (no OOM fallback needed).

        Returns:
            (response_text, model_used)
        """
        result = await self.generate(messages, model=self.smart_model)
        return result, self.model

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list,
        model: str | None = None,
        tool_choice: str = "auto",
    ):
        """Ensure the tier matches the requested model, then delegate to base."""
        await self._ensure_tier(model)
        await self._ensure_alive()
        return await super().generate_with_tools(messages, tools, model=model, tool_choice=tool_choice)

    @property
    def last_tok_s(self) -> float:
        """Generation-only tok/s from the most recent generate() call."""
        if self._call_metrics:
            return self._call_metrics[-1].get("tok_s", 0.0)
        return 0.0
