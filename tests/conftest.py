# tests/conftest.py
"""Shared pytest setup for deterministic local and CI test runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from fitz_forge.config.schema import FitzPlannerConfig

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def configured_test_workspace(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point default config loading at a preconfigured temporary file.

    CI runners start with no user config. Production ``load_config()`` should
    launch the first-run wizard in that situation, but unit tests must never
    read from stdin unless the test explicitly patches that path.
    """
    from fitz_forge.config import loader

    config_path = tmp_path_factory.mktemp("fitz_forge_config") / "config.yaml"
    config = FitzPlannerConfig().model_dump(mode="json")
    config["lm_studio"]["model"] = "test-model"
    config_path.write_text(
        yaml.safe_dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "get_config_path", lambda: config_path)
