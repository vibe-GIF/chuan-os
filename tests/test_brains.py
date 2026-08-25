"""N1 大脑层测试 —— brain 注册表与统一完成接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from chuan.brains import Brain, BrainRegistry


# ------------------------------------------------------------------ #
# 夹具：构造临时 config/secrets
# ------------------------------------------------------------------ #
@pytest.fixture
def fake_configs(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "config"
    config.mkdir()
    cfg = config / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "brains": {
                    "cloud_general": {
                        "provider": "openrouter",
                        "model": "openai/gpt-3.5-turbo",
                        "api_key_env": "TEST_OPENROUTER_KEY",
                    },
                    "local": {
                        "provider": "ollama",
                        "model": "qwen2.5:14b",
                        "base_url": "http://localhost:11434",
                    },
                },
                "routing": {
                    "default_brain": "cloud_general",
                    "fallback_brain": "local",
                },
            }
        )
    )
    sec = config / "secrets.yaml"
    sec.write_text(yaml.safe_dump({"openrouter_api_key": "sk-test-key"}))
    return cfg, sec


def test_brain_registry_loads_brains(fake_configs: tuple[Path, Path]) -> None:
    cfg, sec = fake_configs
    registry = BrainRegistry(config_path=cfg, secrets_path=sec)
    names = registry.list()
    assert "cloud_general" in names
    assert "local" in names


def test_brain_registry_default_and_fallback(fake_configs: tuple[Path, Path]) -> None:
    cfg, sec = fake_configs
    registry = BrainRegistry(config_path=cfg, secrets_path=sec)
    default = registry.default()
    assert default.name == "cloud_general"
    fallback = registry.fallback()
    assert fallback is not None and fallback.name == "local"


def test_brain_registry_get_returns_none_for_missing(fake_configs: tuple[Path, Path]) -> None:
    cfg, sec = fake_configs
    registry = BrainRegistry(config_path=cfg, secrets_path=sec)
    assert registry.get("nonexistent") is None


def test_brain_registry_contains(fake_configs: tuple[Path, Path]) -> None:
    cfg, sec = fake_configs
    registry = BrainRegistry(config_path=cfg, secrets_path=sec)
    assert "cloud_general" in registry
    assert "imaginary" not in registry


def test_brain_complete_converts_string_to_message() -> None:
    registry = BrainRegistry()
    brain = registry.get("cloud_general") or registry.get("local")
    if brain is None:
        pytest.skip("no brain available — requires API key or Ollama")
    if not _has_api_key(brain):
        pytest.skip("API key not configured — skipping live call")
    reply = brain.complete("say 'hello'")
    assert isinstance(reply, str)
    assert len(reply) > 0


def test_brain_complete_with_message_list() -> None:
    registry = BrainRegistry()
    brain = registry.get("cloud_general") or registry.get("local")
    if brain is None:
        pytest.skip("no brain available — requires API key or Ollama")
    if not _has_api_key(brain):
        pytest.skip("API key not configured — skipping live call")
    reply = brain.complete(
        [{"role": "user", "content": "say 'hi'"}], system="respond in one word"
    )
    assert isinstance(reply, str)
    assert len(reply) > 0


def _has_api_key(brain: Brain) -> bool:
    model = brain.model
    api_key = getattr(model, "api_key", None)
    if api_key and api_key != "sk-dummy":
        return True
    base_url = str(getattr(model, "base_url", ""))
    return "ollama" in base_url or "localhost" in base_url


def test_brain_skips_unknown_provider(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    config = cfg / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "brains": {
                    "unknown_brain": {
                        "provider": "nonexistent_provider",
                        "model": "foo",
                    },
                },
            }
        )
    )
    sec = cfg / "secrets.yaml"
    sec.write_text("")
    registry = BrainRegistry(config_path=config, secrets_path=sec)
    assert registry.list() == []


def test_brain_empty_config_returns_empty_list(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "config.yaml").write_text("")
    (cfg / "secrets.yaml").write_text("")
    registry = BrainRegistry(config_path=cfg / "config.yaml", secrets_path=cfg / "secrets.yaml")
    assert registry.list() == []


def test_brain_openrouter_uses_env_var(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "env-key-value")
    cfg = tmp_path / "config"
    cfg.mkdir()
    config = cfg / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "brains": {
                    "cloud_general": {
                        "provider": "openrouter",
                        "model": "openai/gpt-3.5-turbo",
                        "api_key_env": "TEST_OPENROUTER_KEY",
                    },
                },
            }
        )
    )
    sec = cfg / "secrets.yaml"
    sec.write_text(yaml.safe_dump({"openrouter_api_key": "fallback-key"}))
    registry = BrainRegistry(config_path=config, secrets_path=sec)
    brain = registry.get("cloud_general")
    assert brain is not None
    assert brain.model.openai_api_key.get_secret_value() == "env-key-value"