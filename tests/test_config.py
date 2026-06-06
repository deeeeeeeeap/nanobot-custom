import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nanobot.cli.commands import _make_provider
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, ResultStorageConfig
from nanobot.exceptions import ConfigError
from nanobot.providers.codex_provider import CodexProvider
from nanobot.providers.openai_responses_provider import OpenAIResponsesProvider


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
    assert cfg.memory.max_message_chars == 4000
    assert cfg.agents.defaults.max_tool_iterations == 50
    assert cfg.agents.defaults.loop_break_threshold == 25
    assert cfg.agents.defaults.max_exempt_rounds == 4
    assert cfg.agents.defaults.max_message_calls_per_turn == 5
    assert cfg.agents.defaults.model_fallbacks == []
    assert cfg.agents.defaults.reasoning_effort == "medium"
    assert cfg.agents.defaults.compaction_enabled is True
    assert cfg.agents.defaults.compaction_target_ratio == 0.45
    assert cfg.logging.max_file_bytes == 500 * 1024 * 1024
    assert cfg.logging.max_files == 5
    assert cfg.providers.codex.enabled is False
    assert cfg.providers.codex.server_compaction_enabled is False
    assert cfg.providers.codex.compact_threshold == 80000
    assert cfg.tools.result_storage.enabled is True
    assert cfg.tools.result_storage.threshold_chars == 8000
    assert cfg.tools.result_storage.turn_budget_chars == 60000
    assert cfg.tools.result_storage.path == "tool-results"
    assert cfg.tools.result_storage.max_files == 500
    assert cfg.tools.result_storage.max_bytes == 256 * 1024 * 1024
    assert cfg.tools.result_storage.max_age_days == 30

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
    cfg_with_reasoning = Config.model_validate(
        {"agents": {"defaults": {"reasoning_effort": "HIGH"}}}
    )
    assert cfg_with_reasoning.agents.defaults.reasoning_effort == "high"
    cfg_without_reasoning = Config.model_validate(
        {"agents": {"defaults": {"reasoning_effort": "none"}}}
    )
    assert cfg_without_reasoning.agents.defaults.reasoning_effort == "none"
    with pytest.raises(ValidationError):
        Config.model_validate({"agents": {"defaults": {"reasoning_effort": "extreme"}}})
    with pytest.raises(ValidationError):
        Config.model_validate({"logging": {"level": "nope"}})
    with pytest.raises(ValidationError):
        Config.model_validate({"agents": {"defaults": {"compaction_target_ratio": 1.2}}})
    with pytest.raises(ValidationError):
        Config.model_validate({"providers": {"codex": {"timeout": 5}}})
    with pytest.raises(ValidationError):
        ResultStorageConfig(path="/tmp/outside")


def test_load_config_accepts_result_storage_camel_case(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    data = {
        "tools": {
            "resultStorage": {
                "enabled": True,
                "thresholdChars": 12345,
                "turnBudgetChars": 70000,
                "path": "tool-results",
                "previewChars": 2500,
                "maxFiles": 7,
                "maxBytes": 1234567,
                "maxAgeDays": 9,
            }
        }
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")

    config = load_config(config_path)
    assert config.tools.result_storage.enabled is True
    assert config.tools.result_storage.threshold_chars == 12345
    assert config.tools.result_storage.turn_budget_chars == 70000
    assert config.tools.result_storage.preview_chars == 2500
    assert config.tools.result_storage.max_files == 7
    assert config.tools.result_storage.max_bytes == 1234567
    assert config.tools.result_storage.max_age_days == 9


def test_load_config_accepts_provider_api_type_and_extra_body(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    data = {
        "providers": {
            "openai": {
                "apiKey": "sk-test",
                "apiBase": "https://relay.example/v1",
                "apiType": "responses",
                "extraHeaders": {"X-Relay": "yes"},
                "extraBody": {"parallel_tool_calls": False},
            }
        }
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")

    config = load_config(config_path)
    assert config.providers.openai.api_type == "responses"
    assert config.providers.openai.extra_headers == {"X-Relay": "yes"}
    assert config.providers.openai.extra_body == {"parallel_tool_calls": False}

    out_path = tmp_path / "saved.json"
    save_config(config, out_path)
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["providers"]["openai"]["extraHeaders"] == {"X-Relay": "yes"}
    assert saved["providers"]["openai"]["extraBody"] == {"parallel_tool_calls": False}


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


def test_make_provider_selects_responses_provider() -> None:
    cfg = Config.model_validate(
        {
            "agents": {"defaults": {"model": "openai/gpt-5-mini"}},
            "providers": {
                "openai": {
                    "api_key": "sk-test",
                    "api_base": "https://relay.example/v1",
                    "api_type": "responses",
                    "extra_body": {"parallel_tool_calls": False},
                }
            },
        }
    )
    provider, fallback = _make_provider(cfg)
    assert isinstance(provider, OpenAIResponsesProvider)
    assert fallback is None
    assert provider.extra_body == {"parallel_tool_calls": False}


def test_commands_agentloop_calls_pass_reasoning_effort() -> None:
    commands_source = Path("nanobot/cli/commands.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(commands_source)

    agent_loop_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "AgentLoop":
            agent_loop_calls.append(node)

    assert agent_loop_calls
    for call in agent_loop_calls:
        keyword_names = {kw.arg for kw in call.keywords if kw.arg is not None}
        assert "reasoning_effort" in keyword_names
