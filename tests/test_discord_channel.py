from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, _split_message
from nanobot.config.schema import DiscordConfig


def test_split_message_respects_discord_limit() -> None:
    parts = _split_message("a" * 4501, max_len=2000)
    assert [len(p) for p in parts] == [2000, 2000, 501]


async def test_discord_send_splits_and_replies_on_first_chunk_only() -> None:
    class _DummyResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {}

    class _DummyHTTP:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def post(self, url, headers=None, json=None):
            self.payloads.append(json or {})
            return _DummyResponse()

    channel = DiscordChannel(DiscordConfig(token="x"), MessageBus())
    channel._http = _DummyHTTP()

    await channel.send(
        OutboundMessage(
            channel="discord",
            chat_id="123",
            content="a" * 4501,
            reply_to="orig-msg-id",
        )
    )

    payloads = channel._http.payloads
    assert len(payloads) == 3
    assert payloads[0]["message_reference"]["message_id"] == "orig-msg-id"
    assert "message_reference" not in payloads[1]
    assert "message_reference" not in payloads[2]
