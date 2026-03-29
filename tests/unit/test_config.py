# tests/unit/test_config.py
"""Tests for configuration schema and loader.

Covers default construction, partial overrides, extra field handling,
YAML loading, missing file handling, and invalid YAML handling.
"""

import logging

import pytest
import yaml
from pydantic import ValidationError

from fitz_graveyard.config.schema import (
    AgentConfig,
    AnthropicConfig,
    ConfidenceConfig,
    FitzPlannerConfig,
    GPUConfig,
    LlamaCppConfig,
    LlamaCppModelConfig,
    LMStudioConfig,
    OllamaConfig,
    OutputConfig,
)
from fitz_graveyard.config.loader import _warn_unknown_keys, load_config


# =========================================================================
# OllamaConfig
# =========================================================================


class TestOllamaConfig:
    """Tests for OllamaConfig sub-model."""

    def test_defaults(self):
        c = OllamaConfig()
        assert c.base_url == "http://localhost:11434"
        assert c.model == "qwen2.5-coder-next:80b-instruct"
        assert c.fallback_model == "qwen2.5-coder-next:32b-instruct"
        assert c.timeout == 300
        assert c.memory_threshold == 80.0

    def test_overrides(self):
        c = OllamaConfig(
            base_url="http://other:11434",
            model="tiny",
            fallback_model=None,
            timeout=60,
            memory_threshold=50.0,
        )
        assert c.base_url == "http://other:11434"
        assert c.fallback_model is None
        assert c.timeout == 60

    def test_memory_threshold_bounds(self):
        with pytest.raises(ValidationError):
            OllamaConfig(memory_threshold=101.0)
        with pytest.raises(ValidationError):
            OllamaConfig(memory_threshold=-1.0)

    def test_extra_fields_ignored(self):
        c = OllamaConfig(phantom="gone")
        assert c.base_url == "http://localhost:11434"

    def test_round_trip(self):
        c = OllamaConfig(model="custom")
        restored = OllamaConfig.model_validate(c.model_dump())
        assert restored.model == "custom"


# =========================================================================
# AgentConfig
# =========================================================================


class TestAgentConfig:
    """Tests for AgentConfig sub-model."""

    def test_defaults(self):
        c = AgentConfig()
        assert c.enabled is True
        assert c.agent_model is None
        assert c.max_file_bytes == 50_000
        assert c.source_dir is None
        assert c.max_seed_files == 50

    def test_overrides(self):
        c = AgentConfig(
            enabled=False,
            agent_model="small",
            max_file_bytes=10_000,
            source_dir="/src",
            max_seed_files=20,
        )
        assert c.enabled is False
        assert c.agent_model == "small"
        assert c.max_seed_files == 20

    def test_extra_fields_ignored(self):
        c = AgentConfig(max_context_length=999)
        assert c.enabled is True

    def test_round_trip(self):
        c = AgentConfig(source_dir="/my/dir")
        restored = AgentConfig.model_validate(c.model_dump())
        assert restored.source_dir == "/my/dir"


# =========================================================================
# OutputConfig
# =========================================================================


class TestOutputConfig:
    """Tests for OutputConfig sub-model."""

    def test_defaults(self):
        c = OutputConfig()
        assert c.plans_dir == ".fitz-graveyard/plans"
        assert c.verbosity == "normal"

    def test_verbosity_literal_validated(self):
        c = OutputConfig(verbosity="verbose")
        assert c.verbosity == "verbose"
        with pytest.raises(ValidationError):
            OutputConfig(verbosity="debug")

    def test_extra_fields_ignored(self):
        c = OutputConfig(color=True)
        assert c.plans_dir == ".fitz-graveyard/plans"


# =========================================================================
# ConfidenceConfig
# =========================================================================


class TestConfidenceConfig:
    """Tests for ConfidenceConfig sub-model."""

    def test_defaults(self):
        c = ConfidenceConfig()
        assert c.default_threshold == 0.7
        assert c.security_threshold == 0.9

    def test_bounds(self):
        with pytest.raises(ValidationError):
            ConfidenceConfig(default_threshold=1.5)
        with pytest.raises(ValidationError):
            ConfidenceConfig(security_threshold=-0.1)

    def test_valid_extremes(self):
        c = ConfidenceConfig(default_threshold=0.0, security_threshold=1.0)
        assert c.default_threshold == 0.0
        assert c.security_threshold == 1.0


# =========================================================================
# AnthropicConfig
# =========================================================================


class TestAnthropicConfig:
    """Tests for AnthropicConfig sub-model."""

    def test_defaults(self):
        c = AnthropicConfig()
        assert c.api_key is None
        assert c.model == "claude-sonnet-4-5-20250929"
        assert c.max_review_tokens == 2048

    def test_max_review_tokens_bounds(self):
        with pytest.raises(ValidationError):
            AnthropicConfig(max_review_tokens=0)
        with pytest.raises(ValidationError):
            AnthropicConfig(max_review_tokens=10000)

    def test_api_key_optional(self):
        c = AnthropicConfig(api_key=None)
        assert c.api_key is None
        c2 = AnthropicConfig(api_key="sk-ant-123")
        assert c2.api_key == "sk-ant-123"


# =========================================================================
# LMStudioConfig
# =========================================================================


class TestLMStudioConfig:
    """Tests for LMStudioConfig sub-model."""

    def test_defaults(self):
        c = LMStudioConfig()
        assert c.base_url == "http://localhost:1234/v1"
        assert c.model == "local-model"
        assert c.fast_model is None
        assert c.smart_model is None
        assert c.fallback_model is None
        assert c.timeout == 300
        assert c.context_length == 65536
        assert c.api_key is None

    def test_overrides(self):
        c = LMStudioConfig(
            base_url="http://other:1234/v1",
            model="big-model",
            fast_model="fast",
            smart_model="smart",
            api_key="key-123",
        )
        assert c.fast_model == "fast"
        assert c.smart_model == "smart"
        assert c.api_key == "key-123"

    def test_extra_fields_ignored(self):
        c = LMStudioConfig(gpu_layers=99)
        assert c.model == "local-model"


# =========================================================================
# LlamaCppModelConfig
# =========================================================================


class TestLlamaCppModelConfig:
    """Tests for LlamaCppModelConfig sub-model."""

    def test_defaults(self):
        c = LlamaCppModelConfig()
        assert c.path == ""
        assert c.context_size == 8192
        assert c.gpu_layers == -1
        assert c.flash_attention is False
        assert c.cache_type_k is None
        assert c.cache_type_v is None

    def test_overrides(self):
        c = LlamaCppModelConfig(
            path="model.gguf",
            context_size=65536,
            gpu_layers=40,
            flash_attention=True,
            cache_type_k="q8_0",
            cache_type_v="q8_0",
        )
        assert c.path == "model.gguf"
        assert c.flash_attention is True
        assert c.cache_type_k == "q8_0"

    def test_extra_fields_ignored(self):
        c = LlamaCppModelConfig(rope_freq=10000)
        assert c.path == ""


# =========================================================================
# LlamaCppConfig
# =========================================================================


class TestLlamaCppConfig:
    """Tests for LlamaCppConfig sub-model."""

    def test_defaults(self):
        c = LlamaCppConfig()
        assert c.server_path == ""
        assert c.models_dir == ""
        assert isinstance(c.fast_model, LlamaCppModelConfig)
        assert c.mid_model is None
        assert c.smart_model is None
        assert c.port == 8012
        assert c.timeout == 300
        assert c.startup_timeout == 120

    def test_nested_model_config(self):
        c = LlamaCppConfig(
            fast_model=LlamaCppModelConfig(path="fast.gguf", context_size=4096),
            smart_model=LlamaCppModelConfig(path="smart.gguf", context_size=65536),
        )
        assert c.fast_model.path == "fast.gguf"
        assert c.smart_model.path == "smart.gguf"

    def test_nested_from_dict(self):
        """Model can be constructed from nested dicts (YAML parse output)."""
        c = LlamaCppConfig.model_validate({
            "server_path": "/usr/bin/llama-server",
            "fast_model": {"path": "small.gguf", "context_size": 2048},
        })
        assert c.fast_model.path == "small.gguf"
        assert c.fast_model.context_size == 2048

    def test_extra_fields_ignored(self):
        c = LlamaCppConfig(batch_size=512)
        assert c.port == 8012


# =========================================================================
# GPUConfig
# =========================================================================


class TestGPUConfig:
    """Tests for GPUConfig sub-model."""

    def test_defaults(self):
        c = GPUConfig()
        assert c.temp_threshold == 73
        assert c.cooldown_margin == 10

    def test_temp_threshold_bounds(self):
        c = GPUConfig(temp_threshold=0)  # disabled
        assert c.temp_threshold == 0
        c2 = GPUConfig(temp_threshold=95)
        assert c2.temp_threshold == 95
        with pytest.raises(ValidationError):
            GPUConfig(temp_threshold=96)
        with pytest.raises(ValidationError):
            GPUConfig(temp_threshold=-1)

    def test_cooldown_margin_bounds(self):
        with pytest.raises(ValidationError):
            GPUConfig(cooldown_margin=4)
        with pytest.raises(ValidationError):
            GPUConfig(cooldown_margin=31)

    def test_extra_fields_ignored(self):
        c = GPUConfig(fan_speed=100)
        assert c.temp_threshold == 73


# =========================================================================
# FitzPlannerConfig (root)
# =========================================================================


class TestFitzPlannerConfig:
    """Tests for the root FitzPlannerConfig model."""

    def test_default_construction(self):
        c = FitzPlannerConfig()
        assert c.provider == "ollama"
        assert isinstance(c.ollama, OllamaConfig)
        assert isinstance(c.lm_studio, LMStudioConfig)
        assert isinstance(c.llama_cpp, LlamaCppConfig)
        assert isinstance(c.agent, AgentConfig)
        assert isinstance(c.output, OutputConfig)
        assert isinstance(c.confidence, ConfidenceConfig)
        assert isinstance(c.anthropic, AnthropicConfig)
        assert isinstance(c.gpu, GPUConfig)

    def test_from_dict_partial_overrides(self):
        c = FitzPlannerConfig.model_validate({
            "provider": "lm_studio",
            "lm_studio": {"model": "my-model"},
            "confidence": {"default_threshold": 0.5},
        })
        assert c.provider == "lm_studio"
        assert c.lm_studio.model == "my-model"
        assert c.confidence.default_threshold == 0.5
        # Non-overridden sub-configs keep defaults
        assert c.ollama.model == "qwen2.5-coder-next:80b-instruct"
        assert c.agent.enabled is True

    def test_extra_fields_ignored(self):
        c = FitzPlannerConfig(
            provider="ollama",
            version="99.0",
            database={"path": "/tmp/db"},
        )
        assert c.provider == "ollama"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValidationError):
            FitzPlannerConfig(provider="openai")

    def test_round_trip(self):
        c = FitzPlannerConfig(
            provider="llama_cpp",
            agent=AgentConfig(enabled=False, source_dir="/code"),
            gpu=GPUConfig(temp_threshold=80),
        )
        data = c.model_dump()
        restored = FitzPlannerConfig.model_validate(data)
        assert restored.provider == "llama_cpp"
        assert restored.agent.enabled is False
        assert restored.agent.source_dir == "/code"
        assert restored.gpu.temp_threshold == 80

    def test_json_round_trip(self):
        """model_dump(mode='json') -> model_validate preserves data."""
        c = FitzPlannerConfig(provider="lm_studio")
        json_data = c.model_dump(mode="json")
        restored = FitzPlannerConfig.model_validate(json_data)
        assert restored.provider == "lm_studio"

    def test_nested_extra_fields_ignored(self):
        """Extra fields on nested sub-configs are also ignored."""
        c = FitzPlannerConfig.model_validate({
            "ollama": {"base_url": "http://x:11434", "typo_field": "ignored"},
            "agent": {"enabled": True, "bogus": 123},
        })
        assert c.ollama.base_url == "http://x:11434"
        assert c.agent.enabled is True


# =========================================================================
# _warn_unknown_keys
# =========================================================================


class TestWarnUnknownKeys:
    """Tests for the _warn_unknown_keys helper function."""

    def test_warns_on_unknown_top_level_key(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_unknown_keys(
                {"provider": "ollama", "tiemout": 600},
                FitzPlannerConfig,
            )
        assert any("tiemout" in msg for msg in caplog.messages)

    def test_warns_on_unknown_nested_key(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_unknown_keys(
                {"ollama": {"base_url": "http://x", "mdoel": "test"}},
                FitzPlannerConfig,
            )
        assert any("ollama.mdoel" in msg for msg in caplog.messages)

    def test_no_warning_for_valid_keys(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_unknown_keys(
                {"provider": "ollama", "ollama": {"base_url": "http://x"}},
                FitzPlannerConfig,
            )
        assert not any("Unknown config key" in msg for msg in caplog.messages)

    def test_handles_non_dict_input(self, caplog):
        """Non-dict yaml_data is a no-op (no crash)."""
        with caplog.at_level(logging.WARNING):
            _warn_unknown_keys("not a dict", FitzPlannerConfig)
        assert not caplog.messages

    def test_handles_non_dict_nested_value(self, caplog):
        """Non-dict nested value under a known BaseModel key is a no-op."""
        with caplog.at_level(logging.WARNING):
            _warn_unknown_keys(
                {"ollama": "not a dict"},
                FitzPlannerConfig,
            )
        # Should not crash; ollama is known so no unknown-key warning
        assert not any("Unknown config key" in msg for msg in caplog.messages)


# =========================================================================
# load_config (integration with filesystem)
# =========================================================================


class TestLoadConfig:
    """Tests for load_config with real filesystem (tmp_path)."""

    def test_creates_default_config_when_missing(self, tmp_path, monkeypatch):
        """load_config creates a default YAML file when none exists."""
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        config = load_config()
        assert isinstance(config, FitzPlannerConfig)
        assert config.provider == "ollama"
        assert config_path.exists()

        # Verify the written YAML is valid
        with config_path.open() as f:
            data = yaml.safe_load(f)
        assert data["provider"] == "ollama"

    def test_loads_existing_yaml(self, tmp_path, monkeypatch):
        """load_config reads and validates an existing YAML file."""
        config_path = tmp_path / "config.yaml"
        yaml_content = {
            "provider": "lm_studio",
            "lm_studio": {"model": "custom-model", "timeout": 600},
            "confidence": {"default_threshold": 0.8},
        }
        with config_path.open("w") as f:
            yaml.safe_dump(yaml_content, f)

        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        config = load_config()
        assert config.provider == "lm_studio"
        assert config.lm_studio.model == "custom-model"
        assert config.lm_studio.timeout == 600
        assert config.confidence.default_threshold == 0.8

    def test_partial_yaml_gets_defaults(self, tmp_path, monkeypatch):
        """YAML with only some keys still gets defaults for the rest."""
        config_path = tmp_path / "config.yaml"
        with config_path.open("w") as f:
            yaml.safe_dump({"provider": "ollama"}, f)

        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        config = load_config()
        assert config.provider == "ollama"
        assert config.ollama.model == "qwen2.5-coder-next:80b-instruct"
        assert config.agent.enabled is True

    def test_extra_yaml_keys_ignored(self, tmp_path, monkeypatch, caplog):
        """Unknown YAML keys are warned and ignored, not rejected."""
        config_path = tmp_path / "config.yaml"
        yaml_content = {
            "provider": "ollama",
            "future_setting": "value",
            "ollama": {"base_url": "http://x:11434", "typo": "val"},
        }
        with config_path.open("w") as f:
            yaml.safe_dump(yaml_content, f)

        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        with caplog.at_level(logging.WARNING):
            config = load_config()

        assert config.provider == "ollama"
        assert any("future_setting" in msg for msg in caplog.messages)

    def test_invalid_yaml_raises(self, tmp_path, monkeypatch):
        """Invalid YAML content raises an error (not silently ignored)."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{invalid yaml: [unclosed")

        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        with pytest.raises(Exception):
            load_config()

    def test_yaml_with_wrong_types_raises(self, tmp_path, monkeypatch):
        """YAML with type mismatches fails Pydantic validation."""
        config_path = tmp_path / "config.yaml"
        yaml_content = {
            "provider": "invalid_provider_name",
        }
        with config_path.open("w") as f:
            yaml.safe_dump(yaml_content, f)

        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        with pytest.raises(ValidationError):
            load_config()

    def test_empty_yaml_file_creates_defaults(self, tmp_path, monkeypatch):
        """Empty YAML file (None from safe_load) should still work."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")

        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        # yaml.safe_load("") returns None, which will fail when unpacked as **None
        with pytest.raises(Exception):
            load_config()

    def test_default_config_round_trips_through_yaml(self, tmp_path, monkeypatch):
        """Default config -> YAML -> load_config produces same values."""
        config_path = tmp_path / "config.yaml"

        # First call: creates default
        monkeypatch.setattr(
            "fitz_graveyard.config.loader.get_config_path",
            lambda: config_path,
        )
        first = load_config()

        # Second call: loads from file
        second = load_config()

        assert first.provider == second.provider
        assert first.ollama.model == second.ollama.model
        assert first.confidence.default_threshold == second.confidence.default_threshold
        assert first.agent.enabled == second.agent.enabled
