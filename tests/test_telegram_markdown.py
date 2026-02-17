from nanobot.bus.queue import MessageBus
from nanobot.channels.telegram import TelegramChannel
from nanobot.config.schema import TelegramConfig
from nanobot.channels.telegram import _markdown_to_telegram_html


def test_markdown_list_prefix_is_not_mojibake() -> None:
    text = "- 时间正常\n- 系统稳定\n- 服务正常"
    out = _markdown_to_telegram_html(text)
    assert "鈥" not in out
    assert out.splitlines()[0].startswith("- ")


def test_split_message_splits_long_single_line() -> None:
    ch = TelegramChannel(TelegramConfig(token="x"), MessageBus())
    parts = ch._split_message("a" * 25, 10)
    assert parts == ["a" * 10, "a" * 10, "a" * 5]


class _DummyUser:
    def __init__(self):
        self.id = 123
        self.username = "alice"
        self.first_name = "Alice"


class _DummyChat:
    def __init__(self):
        self.type = "private"


class _DummyMessage:
    def __init__(self):
        self.chat_id = 456
        self.message_id = 789
        self.chat = _DummyChat()


class _DummyUpdate:
    def __init__(self):
        self.message = _DummyMessage()
        self.effective_user = _DummyUser()


async def test_on_new_forwards_to_agent(monkeypatch) -> None:
    ch = TelegramChannel(TelegramConfig(token="x"), MessageBus())
    seen = {}

    async def _fake_handle_message(sender_id, chat_id, content, media=None, metadata=None):
        seen["sender_id"] = sender_id
        seen["chat_id"] = chat_id
        seen["content"] = content
        seen["metadata"] = metadata or {}

    monkeypatch.setattr(ch, "_handle_message", _fake_handle_message)
    await ch._on_new(_DummyUpdate(), None)

    assert seen["sender_id"] == "123|alice"
    assert seen["chat_id"] == "456"
    assert seen["content"] == "/new"
    assert seen["metadata"]["message_id"] == 789
