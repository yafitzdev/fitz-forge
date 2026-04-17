# fitz_forge/config/loader.py
"""
Configuration loading with auto-creation of defaults.

Uses platformdirs for cross-platform config directory management.
"""

import asyncio
import logging
import sys
from pathlib import Path

import typer
import yaml
from platformdirs import user_config_path
from pydantic import BaseModel

from .schema import FitzPlannerConfig

logger = logging.getLogger(__name__)


def get_config_path() -> Path:
    """Get path to config file, ensuring config directory exists."""
    config_dir = user_config_path("fitz-forge", ensure_exists=True)
    return config_dir / "config.yaml"


def _warn_unknown_keys(yaml_data: dict, model_class: type[BaseModel], prefix: str = "") -> None:
    """Log warnings for YAML keys not recognized by the Pydantic model."""
    if not isinstance(yaml_data, dict):
        return
    known = set(model_class.model_fields.keys())
    for key in yaml_data:
        full_key = f"{prefix}.{key}" if prefix else key
        if key not in known:
            logger.warning(f"Unknown config key '{full_key}' — will be ignored. Typo?")
        else:
            field = model_class.model_fields[key]
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                _warn_unknown_keys(yaml_data[key], annotation, full_key)


def _maybe_run_first_time_wizard(config_path: Path) -> None:
    """If config is missing or contains only placeholder values, run the wizard.

    Printing and prompting go to the user's terminal (stdout) because this
    is the interactive setup path. Raises ``typer.Exit`` on Ctrl+C so the
    caller exits cleanly rather than continuing with unconfigured state.
    """
    from . import prep

    if not prep.is_unconfigured(config_path):
        return

    # ``print`` here is the interactive-UX exception (rule #2) — this is
    # triggered from the CLI path, not from MCP. MCP users go through
    # ``fitz prep`` manually before launching the server.
    print("First-time setup — run 'fitz prep' to configure.", file=sys.stderr)

    try:
        asyncio.run(prep.run_wizard(config_path))
    except KeyboardInterrupt:
        print("\nSetup aborted.", file=sys.stderr)
        raise typer.Exit(130) from None


def load_config() -> FitzPlannerConfig:
    """
    Load configuration from YAML file.

    If the config file doesn't exist or still holds placeholder values
    (first run), invokes the ``fitz prep`` setup wizard inline before
    loading. Returns a validated Pydantic model.
    """
    config_path = get_config_path()

    _maybe_run_first_time_wizard(config_path)

    if not config_path.exists():
        # Wizard was skipped/bypassed (e.g. in a test with the wizard patched
        # out) — fall back to writing defaults so the rest of the pipeline
        # has something to work with.
        default_config = FitzPlannerConfig()
        config_dict = default_config.model_dump(mode="json")

        with config_path.open("w") as f:
            yaml.safe_dump(config_dict, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Created default config at {config_path}")
        return default_config

    # Load existing config
    with config_path.open("r") as f:
        config_data = yaml.safe_load(f)

    # Warn on unknown keys before Pydantic silently ignores them
    _warn_unknown_keys(config_data, FitzPlannerConfig)

    # Parse and validate with Pydantic
    config = FitzPlannerConfig(**config_data)
    logger.info(f"Loaded config from {config_path}")
    return config
