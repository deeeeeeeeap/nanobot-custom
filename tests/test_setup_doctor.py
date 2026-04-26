from typer.testing import CliRunner

from nanobot.cli.commands import _apply_vps_profile, _mask_secret, app
from nanobot.config.loader import save_config
from nanobot.config.schema import Config


def test_apply_vps_profile_sets_low_resource_defaults() -> None:
    config = Config()
    _apply_vps_profile(config)

    assert config.search.vector_enabled is False
    assert config.search.auto_index is False
    assert config.agents.defaults.max_tool_iterations == 20
    assert config.agents.defaults.tool_result_max_chars == 8000
    assert config.agents.defaults.compaction_enabled is True
    assert config.logging.max_file_bytes == 50 * 1024 * 1024
    assert config.logging.max_files == 3
    assert config.tools.result_storage.enabled is True
    assert config.tools.result_storage.threshold_chars == 8000


def test_apply_vps_profile_preserves_enabled_optional_channels() -> None:
    config = Config()
    config.channels.slack.enabled = True
    config.channels.discord.enabled = True
    config.channels.feishu.enabled = True
    config.channels.dingtalk.enabled = True
    config.channels.qq.enabled = True
    config.channels.whatsapp.enabled = True

    _apply_vps_profile(config)

    assert config.channels.slack.enabled is True
    assert config.channels.discord.enabled is True
    assert config.channels.feishu.enabled is True
    assert config.channels.dingtalk.enabled is True
    assert config.channels.qq.enabled is True
    assert config.channels.whatsapp.enabled is True


def test_mask_secret_never_returns_full_secret() -> None:
    secret = "telegram-token-super-secret"
    masked = _mask_secret(secret)

    assert secret not in masked
    assert masked.startswith("tel...")
    assert masked.endswith(" chars)")
    assert _mask_secret("") == "<not set>"


def test_doctor_missing_config_is_clear(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "missing" in result.output.lower()
    assert "nanobot setup" in result.output


def test_doctor_does_not_leak_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    config = Config()
    _apply_vps_profile(config)
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.workspace_path.mkdir(parents=True)
    config.channels.telegram.enabled = True
    config.channels.telegram.token = "123456:secret-token-value"
    config.providers.minimax.api_key = "minimax-secret-key"
    save_config(config)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "123456:secret-token-value" not in result.output
    assert "minimax-secret-key" not in result.output
    assert "Doctor complete" in result.output
