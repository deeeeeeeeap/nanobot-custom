import json

import pytest
from pydantic import ValidationError

from nanobot.cli.commands import _make_provider
from nanobot.config.loader import load_config
from nanobot.config.schema import Config
from nanobot.exceptions import ConfigError
from nanobot.providers.codex_provider import CodexProvider


def test_schema_rejects_enabled_telegram_without_token() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"channels": {"telegram": {"enabled": True, "token": ""}}})


def test_schema_rejects_invalid_gateway_port() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"gateway": {"port": 70000}})


def test_schema_rejects_invalid_provider_api_base() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"providers": {"openai": {"api_base": "not-a-url"}}})


def test_load_config_raises_on_invalid_json(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{ invalid json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_raises_on_invalid_schema(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    data = {
        "channels": {
            "telegram": {
                "enabled": True,
                "token": "",
            }
        }
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_accepts_valid_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    data = {
        "channels": {
            "telegram": {
                "enabled": True,
                "token": "abc123",
            }
        },
        "gateway": {"port": 18790},
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")

    config = load_config(config_path)
    assert config.channels.telegram.enabled is True
    assert config.channels.telegram.token == "abc123"


def test_search_config_defaults_and_validation() -> None:
    cfg = Config()
    assert cfg.search.enabled is True
    assert cfg.search.index_dirs == ["memory"]
    assert cfg.search.vector_enabled is False
    assert cfg.memory.auto_compress is True
    assert cfg.memory.compress_threshold == 10
    assert cfg.agents.defaults.max_tool_iterations == 50
    assert cfg.agents.defaults.loop_break_threshold == 25
    assert cfg.agents.defaults.max_exempt_rounds == 4
    assert cfg.agents.defaults.max_message_calls_per_turn == 5
    assert cfg.agents.defaults.model_fallbacks == []
    assert cfg.agents.defaults.compaction_enabled is True
    assert cfg.agents.defaults.compaction_target_ratio == 0.45
    assert cfg.logging.max_file_bytes == 500 * 1024 * 1024
    assert cfg.logging.max_files == 5
    assert cfg.providers.codex.enabled is False
    assert cfg.providers.codex.server_compaction_enabled is False
    assert cfg.providers.codex.compact_threshold == 80000

    with pytest.raises(ValidationError):
        Config.model_validate({"search": {"default_limit": 0}})
    with pytest.raises(ValidationError):
        Config.model_validate({"search": {"embedding_chunk_overlap": 0.9}})
    with pytest.raises(ValidationError):
        Config.model_validate({"search": {"embedding_batch_size": 0}})
    with pytest.raises(ValidationError):
        Config.model_validate({"memory": {"compress_threshold": 0}})
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "agents": {
                    "defaults": {
                        "loop_warn_threshold": 12,
                        "loop_critical_threshold": 8,
                    }
                }
            }
        )
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "agents": {
                    "defaults": {
                        "loop_window": 10,
                        "loop_break_threshold": 11,
                    }
                }
            }
        )
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "agents": {
                    "defaults": {
                        "max_tool_iterations": 5,
                        "max_exempt_rounds": 6,
                    }
                }
            }
        )
    with pytest.raises(ValidationError):
        Config.model_validate({"logging": {"level": "nope"}})
    with pytest.raises(ValidationError):
        Config.model_validate({"agents": {"defaults": {"compaction_target_ratio": 1.2}}})
    with pytest.raises(ValidationError):
        Config.model_validate({"providers": {"codex": {"timeout": 5}}})


def test_make_provider_selects_codex_when_enabled(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "token",
                    "refresh_token": "refresh",
                    "account_id": "acct",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    cfg = Config.model_validate(
        {
            "agents": {"defaults": {"model": "openai/gpt-5.3-codex"}},
            "providers": {"codex": {"enabled": True}},
        }
    )
    provider, fallback = _make_provider(cfg)
    assert isinstance(provider, CodexProvider)
