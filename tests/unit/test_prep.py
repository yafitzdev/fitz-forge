# tests/unit/test_prep.py
"""Unit tests for the fitz prep first-run setup wizard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from fitz_forge.cli import app
from fitz_forge.config.prep import (
    CASCADE_TARGETS,
    fetch_models,
    is_unconfigured,
    probe_servers,
    run_wizard,
    write_config,
)


# ---------------------------------------------------------------------------
# probe helpers
# ---------------------------------------------------------------------------


def _mock_async_client(get_side_effects):
    """Build a mock httpx.AsyncClient with configured get() behavior.

    get_side_effects: list of (status_code|Exception) per call.
    """
    client = MagicMock()

    async def _get(url, **_kwargs):
        # Pop the next side effect off the list.
        item = get_side_effects.pop(0)
        if isinstance(item, Exception):
            raise item
        response = MagicMock()
        response.status_code = item
        return response

    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# probe_servers
# ---------------------------------------------------------------------------


class TestProbeServers:
    @pytest.mark.asyncio
    async def test_single_server_detected(self):
        # LM Studio OK, rest fail.
        effects = [200, ConnectionError(), ConnectionError(), ConnectionError()]
        with patch(
            "fitz_forge.config.prep.httpx.AsyncClient",
            return_value=_mock_async_client(effects),
        ):
            results = await probe_servers()
        assert results[0] == ("http://localhost:1234/v1", "LM Studio", True)
        assert all(not ok for _, _, ok in results[1:])

    @pytest.mark.asyncio
    async def test_cascade_second_wins(self):
        # Only llama-server responds.
        effects = [ConnectionError(), 200, ConnectionError(), ConnectionError()]
        with patch(
            "fitz_forge.config.prep.httpx.AsyncClient",
            return_value=_mock_async_client(effects),
        ):
            results = await probe_servers()
        assert results[0][2] is False
        assert results[1] == ("http://localhost:8080/v1", "llama-server", True)

    @pytest.mark.asyncio
    async def test_no_server_detected(self):
        effects = [ConnectionError() for _ in CASCADE_TARGETS]
        with patch(
            "fitz_forge.config.prep.httpx.AsyncClient",
            return_value=_mock_async_client(effects),
        ):
            results = await probe_servers()
        assert all(not ok for _, _, ok in results)


# ---------------------------------------------------------------------------
# fetch_models
# ---------------------------------------------------------------------------


class TestFetchModels:
    @pytest.mark.asyncio
    async def test_returns_model_ids(self):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json = MagicMock(
            return_value={"data": [{"id": "gemma-3-4b"}, {"id": "qwen3-coder-30b"}]}
        )

        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("fitz_forge.config.prep.httpx.AsyncClient", return_value=client):
            ids = await fetch_models("http://localhost:1234/v1")
        assert ids == ["gemma-3-4b", "qwen3-coder-30b"]

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"data": []})

        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("fitz_forge.config.prep.httpx.AsyncClient", return_value=client):
            ids = await fetch_models("http://localhost:1234/v1")
        assert ids == []


# ---------------------------------------------------------------------------
# write_config + is_unconfigured
# ---------------------------------------------------------------------------


class TestWriteConfig:
    def test_writes_new_config(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        write_config("http://localhost:1234/v1", "qwen3-coder-30b", cfg)

        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["provider"] == "lm_studio"
        assert data["lm_studio"]["base_url"] == "http://localhost:1234/v1"
        assert data["lm_studio"]["model"] == "qwen3-coder-30b"
        assert data["lm_studio"]["timeout"] == 600
        assert data["lm_studio"]["context_length"] == 65536

    def test_preserves_existing_sections(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        existing = {
            "provider": "ollama",
            "ollama": {"base_url": "http://example.com", "model": "x"},
            "agent": {"enabled": False, "max_seed_files": 42},
            "output": {"plans_dir": "custom/plans"},
            "lm_studio": {
                "base_url": "http://localhost:1234/v1",
                "model": "local-model",
                "api_key": "secret",
            },
        }
        cfg.write_text(yaml.safe_dump(existing), encoding="utf-8")

        write_config("http://localhost:8080/v1", "new-model", cfg)

        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["provider"] == "lm_studio"
        assert data["agent"]["enabled"] is False
        assert data["agent"]["max_seed_files"] == 42
        assert data["output"]["plans_dir"] == "custom/plans"
        assert data["ollama"]["base_url"] == "http://example.com"
        assert data["lm_studio"]["base_url"] == "http://localhost:8080/v1"
        assert data["lm_studio"]["model"] == "new-model"
        # existing api_key preserved
        assert data["lm_studio"]["api_key"] == "secret"


class TestIsUnconfigured:
    def test_missing_file(self, tmp_path: Path):
        assert is_unconfigured(tmp_path / "nope.yaml") is True

    def test_empty_model(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.safe_dump({"lm_studio": {"model": ""}}), encoding="utf-8")
        assert is_unconfigured(cfg) is True

    def test_placeholder_model(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.safe_dump({"lm_studio": {"model": "local-model"}}),
            encoding="utf-8",
        )
        assert is_unconfigured(cfg) is True

    def test_real_model(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.safe_dump({"lm_studio": {"model": "qwen3-coder-30b"}}),
            encoding="utf-8",
        )
        assert is_unconfigured(cfg) is False


# ---------------------------------------------------------------------------
# run_wizard — interactive paths
# ---------------------------------------------------------------------------


def _patch_probe(success_url: str | None) -> AsyncMock:
    """Return an AsyncMock for probe_servers that matches success_url."""
    results = [(url, label, url == success_url) for url, label in CASCADE_TARGETS]
    return AsyncMock(return_value=results)


@pytest.mark.asyncio
async def test_wizard_single_server_single_model(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    probe = _patch_probe("http://localhost:1234/v1")
    fetch = AsyncMock(return_value=["solo-model"])

    prompts = iter(["", ""])  # URL default, model default

    def fake_prompt(*args, **kwargs):
        return next(prompts) or kwargs.get("default", "")

    with (
        patch("fitz_forge.config.prep.probe_servers", probe),
        patch("fitz_forge.config.prep.fetch_models", fetch),
        patch("fitz_forge.config.prep.typer.prompt", side_effect=fake_prompt),
    ):
        await run_wizard(cfg)

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["lm_studio"]["base_url"] == "http://localhost:1234/v1"
    assert data["lm_studio"]["model"] == "solo-model"


@pytest.mark.asyncio
async def test_wizard_multi_model_numeric_selection(tmp_path):
    cfg = tmp_path / "config.yaml"
    probe = _patch_probe("http://localhost:1234/v1")
    fetch = AsyncMock(return_value=["alpha", "beta", "gamma"])

    prompts = iter(["", "2"])  # URL default, pick beta

    def fake_prompt(*args, **kwargs):
        val = next(prompts)
        return val if val else kwargs.get("default", "")

    with (
        patch("fitz_forge.config.prep.probe_servers", probe),
        patch("fitz_forge.config.prep.fetch_models", fetch),
        patch("fitz_forge.config.prep.typer.prompt", side_effect=fake_prompt),
    ):
        await run_wizard(cfg)

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["lm_studio"]["model"] == "beta"


@pytest.mark.asyncio
async def test_wizard_no_server_manual_url(tmp_path):
    cfg = tmp_path / "config.yaml"
    probe = _patch_probe(None)
    fetch = AsyncMock(return_value=["hand-picked"])

    prompts = iter(["http://example.com/v1", ""])  # manual URL, default model

    def fake_prompt(*args, **kwargs):
        val = next(prompts)
        return val if val else kwargs.get("default", "")

    with (
        patch("fitz_forge.config.prep.probe_servers", probe),
        patch("fitz_forge.config.prep.fetch_models", fetch),
        patch("fitz_forge.config.prep.typer.prompt", side_effect=fake_prompt),
    ):
        await run_wizard(cfg)

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["lm_studio"]["base_url"] == "http://example.com/v1"
    assert data["lm_studio"]["model"] == "hand-picked"


@pytest.mark.asyncio
async def test_wizard_zero_models_loops_until_input(tmp_path):
    cfg = tmp_path / "config.yaml"
    probe = _patch_probe("http://localhost:1234/v1")
    fetch = AsyncMock(return_value=[])

    # URL default, then empty string (rejected), then real id.
    prompts = iter(["", "", "manual-model-id"])

    def fake_prompt(*args, **kwargs):
        val = next(prompts)
        return val if val else kwargs.get("default", "")

    with (
        patch("fitz_forge.config.prep.probe_servers", probe),
        patch("fitz_forge.config.prep.fetch_models", fetch),
        patch("fitz_forge.config.prep.typer.prompt", side_effect=fake_prompt),
    ):
        await run_wizard(cfg)

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["lm_studio"]["model"] == "manual-model-id"


# ---------------------------------------------------------------------------
# run_wizard — flag overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wizard_both_flags_skips_prompts(tmp_path):
    cfg = tmp_path / "config.yaml"
    fetch = AsyncMock(return_value=["flag-model"])
    probe = AsyncMock()

    with (
        patch("fitz_forge.config.prep.probe_servers", probe),
        patch("fitz_forge.config.prep.fetch_models", fetch),
        patch("fitz_forge.config.prep.typer.prompt") as prompt_mock,
    ):
        await run_wizard(
            cfg,
            base_url="http://localhost:1234/v1",
            model="flag-model",
        )
        prompt_mock.assert_not_called()

    probe.assert_not_called()
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["lm_studio"]["base_url"] == "http://localhost:1234/v1"
    assert data["lm_studio"]["model"] == "flag-model"


@pytest.mark.asyncio
async def test_wizard_base_url_only_prompts_for_model(tmp_path):
    cfg = tmp_path / "config.yaml"
    fetch = AsyncMock(return_value=["alpha"])
    probe = AsyncMock()

    def fake_prompt(*args, **kwargs):
        return kwargs.get("default", "")

    with (
        patch("fitz_forge.config.prep.probe_servers", probe),
        patch("fitz_forge.config.prep.fetch_models", fetch),
        patch("fitz_forge.config.prep.typer.prompt", side_effect=fake_prompt),
    ):
        await run_wizard(cfg, base_url="http://foo/v1")

    probe.assert_not_called()
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["lm_studio"]["base_url"] == "http://foo/v1"
    assert data["lm_studio"]["model"] == "alpha"


# ---------------------------------------------------------------------------
# CLI integration smoke
# ---------------------------------------------------------------------------


class TestCliIntegration:
    def test_prep_help_shows_command(self):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["prep", "--help"],
            color=False,
            env={"NO_COLOR": "1", "COLUMNS": "160"},
        )
        assert result.exit_code == 0
        help_text = result.stdout
        assert "first-run setup wizard" in help_text.lower()
        assert "--base-url" in help_text
        assert "--model" in help_text
        assert "API base URL" in help_text
        assert "Model identifier" in help_text


# ---------------------------------------------------------------------------
# First-run trigger on load_config
# ---------------------------------------------------------------------------


class TestFirstRunTrigger:
    def test_load_config_triggers_wizard_when_unconfigured(self, tmp_path, monkeypatch):
        """If config is missing or model is placeholder, loader invokes wizard."""
        from fitz_forge.config import loader, prep as prep_mod

        cfg_path = tmp_path / "config.yaml"

        invoked = {"count": 0}

        async def fake_wizard(path, base_url=None, model=None):
            # Simulate user completing the wizard.
            write_config("http://localhost:1234/v1", "wizard-wrote-this", path)
            invoked["count"] += 1

        monkeypatch.setattr(loader, "get_config_path", lambda: cfg_path)
        monkeypatch.setattr(prep_mod, "run_wizard", fake_wizard)

        config = loader.load_config()
        assert invoked["count"] == 1
        assert config.provider == "lm_studio"
        assert config.lm_studio.model == "wizard-wrote-this"

    def test_load_config_no_wizard_when_configured(self, tmp_path, monkeypatch):
        from fitz_forge.config import loader, prep as prep_mod

        cfg_path = tmp_path / "config.yaml"
        write_config("http://localhost:1234/v1", "already-configured", cfg_path)

        monkeypatch.setattr(loader, "get_config_path", lambda: cfg_path)

        called = {"count": 0}

        async def fake_wizard(path, base_url=None, model=None):
            called["count"] += 1

        monkeypatch.setattr(prep_mod, "run_wizard", fake_wizard)

        config = loader.load_config()
        assert called["count"] == 0
        assert config.lm_studio.model == "already-configured"

    def test_load_config_placeholder_triggers_wizard(self, tmp_path, monkeypatch):
        from fitz_forge.config import loader, prep as prep_mod

        cfg_path = tmp_path / "config.yaml"
        # Pre-write a config with the placeholder "local-model" value.
        cfg_path.write_text(
            yaml.safe_dump({"provider": "lm_studio", "lm_studio": {"model": "local-model"}}),
            encoding="utf-8",
        )

        invoked = {"count": 0}

        async def fake_wizard(path, base_url=None, model=None):
            write_config("http://localhost:1234/v1", "real-model", path)
            invoked["count"] += 1

        monkeypatch.setattr(loader, "get_config_path", lambda: cfg_path)
        monkeypatch.setattr(prep_mod, "run_wizard", fake_wizard)

        config = loader.load_config()
        assert invoked["count"] == 1
        assert config.lm_studio.model == "real-model"
