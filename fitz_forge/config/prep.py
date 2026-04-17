# fitz_forge/config/prep.py
"""
First-run setup wizard for fitz-forge.

Cascade-probes common local OpenAI-compatible API servers, fetches the
``/v1/models`` list, and writes a ``config.yaml`` that pins the chosen
``provider``/``base_url``/``model``. Existing config sections are
preserved verbatim — only ``provider`` and ``lm_studio`` are rewritten.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import typer
import yaml

from .schema import FitzPlannerConfig, LMStudioConfig

logger = logging.getLogger(__name__)


# Ordered cascade: first server returning HTTP 200 on /models wins.
CASCADE_TARGETS: list[tuple[str, str]] = [
    ("http://localhost:1234/v1", "LM Studio"),
    ("http://localhost:8080/v1", "llama-server"),
    ("http://localhost:8000/v1", "vLLM"),
    ("http://localhost:30000/v1", "SGLang"),
]


# Placeholder model strings that mean "unconfigured".
_PLACEHOLDER_MODELS = {"", "local-model"}


def is_unconfigured(config_path: Path) -> bool:
    """Return True if the user has not yet completed first-run setup."""
    if not config_path.exists():
        return True
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return True
    model = ((data.get("lm_studio") or {}).get("model")) or ""
    return model in _PLACEHOLDER_MODELS


async def _probe_one(
    client: httpx.AsyncClient, base_url: str
) -> bool:
    """Probe a single base URL. Returns True iff GET {base_url}/models -> 200."""
    try:
        response = await client.get(f"{base_url}/models", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


async def probe_servers(
    targets: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str, bool]]:
    """Probe each cascade target. Returns [(base_url, label, reachable)]."""
    targets = targets or CASCADE_TARGETS
    results: list[tuple[str, str, bool]] = []
    async with httpx.AsyncClient() as client:
        for base_url, label in targets:
            ok = await _probe_one(client, base_url)
            results.append((base_url, label, ok))
    return results


async def fetch_models(base_url: str) -> list[str]:
    """Fetch model identifiers from a running server. Raises on failure."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{base_url}/models")
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") or []
    ids: list[str] = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("id"):
            ids.append(str(entry["id"]))
    return ids


def _load_existing_raw(config_path: Path) -> dict:
    """Return the raw YAML dict on disk (empty dict if missing/invalid)."""
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Could not parse existing config at {config_path}: {e}")
        return {}


def write_config(base_url: str, model: str, config_path: Path) -> None:
    """Write provider/lm_studio into config.yaml, preserving other sections."""
    existing = _load_existing_raw(config_path)

    existing["provider"] = "lm_studio"
    lm_studio = existing.get("lm_studio") or {}
    lm_studio["base_url"] = base_url
    lm_studio["model"] = model
    lm_studio.setdefault("timeout", 600)
    lm_studio.setdefault("context_length", 65536)
    existing["lm_studio"] = lm_studio

    # Validate round-trip through the Pydantic schema so we never
    # write a config that ``load_config`` will reject.
    FitzPlannerConfig(**existing)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)


def _prompt_model_choice(models: list[str]) -> str:
    """Interactively pick a model id from the fetched list."""
    if len(models) == 1:
        chosen = typer.prompt("Model identifier", default=models[0])
        return chosen.strip() or models[0]

    # Multiple models — numbered menu, default 1, accept number or raw text.
    for i, m in enumerate(models, 1):
        typer.echo(f"  {i}. {m}")
    raw = typer.prompt("Select model", default="1")
    raw = raw.strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(models):
            return models[idx]
    return raw


async def _verify_combo(base_url: str, model: str) -> None:
    """Ensure base_url serves model. Raises typer.Exit on failure."""
    try:
        models = await fetch_models(base_url)
    except Exception as e:
        typer.echo(f"ERROR: could not reach {base_url}: {e}", err=True)
        raise typer.Exit(1) from None
    if model not in models and models:
        typer.echo(
            f"WARNING: model '{model}' not in server's model list "
            f"({', '.join(models)}). Writing config anyway.",
            err=True,
        )


async def run_wizard(
    config_path: Path,
    base_url: str | None = None,
    model: str | None = None,
) -> None:
    """Run the first-run setup wizard, writing config.yaml on completion.

    If both base_url and model are provided, runs non-interactively.
    If only base_url is provided, probes skipped — prompt only for model.
    Otherwise probes cascade targets and prompts interactively.
    """
    # Non-interactive path: both provided.
    if base_url and model:
        await _verify_combo(base_url, model)
        write_config(base_url, model, config_path)
        typer.echo(f"Saved to {config_path}")
        return

    # Partial: base_url provided, prompt for model.
    if base_url and not model:
        typer.echo(f"Fetching models from {base_url}...")
        try:
            models = await fetch_models(base_url)
        except Exception as e:
            typer.echo(f"ERROR: could not reach {base_url}: {e}", err=True)
            raise typer.Exit(1) from None
        chosen_model = _resolve_model(models)
        write_config(base_url, chosen_model, config_path)
        typer.echo(f"\nSaved to {config_path}")
        typer.echo("Run 'fitz prep' anytime to change these settings.")
        return

    # Full interactive flow.
    typer.echo("Probing local API servers...")
    results = await probe_servers()
    for url, label, ok in results:
        mark = "[OK]" if ok else "[--]"
        typer.echo(f"  {mark} {url}  ({label})")

    default_url = next((url for url, _, ok in results if ok), None)

    if default_url is None:
        typer.echo("\nNo server detected. Enter URL manually:")
        chosen_url = typer.prompt("API base URL")
    else:
        chosen_url = typer.prompt("API base URL", default=default_url)
    chosen_url = chosen_url.strip()

    typer.echo(f"  -> GET {chosen_url}/models")
    try:
        models = await fetch_models(chosen_url)
    except Exception as e:
        typer.echo(f"ERROR: could not reach {chosen_url}: {e}", err=True)
        raise typer.Exit(1) from None

    if not models:
        typer.echo(f"  -> 0 models")
    elif len(models) == 1:
        typer.echo(f"  -> 1 model: {models[0]}")
    else:
        typer.echo(f"  -> {len(models)} models")

    chosen_model = _resolve_model(models)

    # context_length default is 65536 — surfaced via LMStudioConfig defaults
    # and baked in by write_config().
    typer.echo(
        f"Context length set to {LMStudioConfig.model_fields['context_length'].default} "
        f"tokens. Change it in the YAML if you need a different window."
    )

    write_config(chosen_url, chosen_model, config_path)
    typer.echo(f"\nSaved to {config_path}")
    typer.echo("Run 'fitz prep' anytime to change these settings.")


def _resolve_model(models: list[str]) -> str:
    """Pick a model id: prompt if >0, loop until non-empty if 0."""
    if len(models) == 0:
        typer.echo(
            "No model loaded on server. Load one (e.g., in LM Studio) "
            "and enter its identifier:"
        )
        while True:
            raw = typer.prompt("Model identifier").strip()
            if raw:
                return raw
    return _prompt_model_choice(models)
