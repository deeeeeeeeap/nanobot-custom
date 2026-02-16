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
