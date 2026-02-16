import json

import pytest
from pydantic import ValidationError

from nanobot.config.loader import load_config
from nanobot.config.schema import Config
from nanobot.exceptions import ConfigError


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
