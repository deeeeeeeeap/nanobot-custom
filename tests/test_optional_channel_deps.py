import builtins

from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config


def test_disabled_optional_channels_do_not_import_optional_sdks(monkeypatch) -> None:
    blocked_prefixes = ("slack_sdk", "lark_oapi", "dingtalk_stream", "botpy", "socketio")
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(blocked_prefixes):
            raise AssertionError(f"unexpected optional SDK import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    config = Config()
    config.channels.telegram.enabled = False
    config.channels.slack.enabled = False
    config.channels.feishu.enabled = False
    config.channels.dingtalk.enabled = False
    config.channels.qq.enabled = False
    config.channels.mochat.enabled = False

    manager = ChannelManager(config, MessageBus())
    assert manager.enabled_channels == []
