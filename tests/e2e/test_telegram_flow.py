import asyncio
from pathlib import Path
from typing import Any

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.exceptions import ConfigError
from nanobot.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    def __init__(self, reply: str = "ok"):
        super().__init__(api_key=None, api_base=None)
        self.reply = reply

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content=self.reply, finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class _NoHallucination:
    is_hallucination = False
    pattern_name = ""
    confidence = 0.0


def _make_loop(monkeypatch, tmp_path: Path, reply: str = "ok") -> tuple[AgentLoop, MessageBus]:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=DummyProvider(reply=reply),
        workspace=tmp_path,
    )
    return loop, bus


async def _roundtrip(loop: AgentLoop, bus: MessageBus, content: str) -> OutboundMessage:
    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="c1",
                content=content,
            )
        )
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=3)
        return outbound
    finally:
        loop.stop()
        await asyncio.wait_for(run_task, timeout=3)


async def test_new_triggers_compression(monkeypatch, tmp_path: Path) -> None:
    loop, bus = _make_loop(monkeypatch, tmp_path)
    session = loop.sessions.get_or_create("telegram:c1")
    session.add_message("user", "hello")
    session.add_message("assistant", "world")
    loop.sessions.save(session)

    called = False

    class _Result:
        created = 1
        merged = 0
        skipped = 0
        summary = "ok"

    async def _ok_compress(session_obj):
        nonlocal called
        called = True
        return _Result()

    monkeypatch.setattr(loop, "_compress_session_for_new", _ok_compress)

    outbound = await _roundtrip(loop, bus, "/new")
    assert called is True
    assert "已开始新会话。" in outbound.content


async def test_clear_skips_compression(monkeypatch, tmp_path: Path) -> None:
    loop, bus = _make_loop(monkeypatch, tmp_path)
    session = loop.sessions.get_or_create("telegram:c1")
    session.add_message("user", "hello")
    loop.sessions.save(session)

    called = False

    async def _should_not_run(session_obj):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(loop, "_compress_session_for_new", _should_not_run)

    outbound = await _roundtrip(loop, bus, "/clear")
    assert called is False
    assert "会话已清空（删除 1 条消息）。" == outbound.content


async def test_help_lists_all_commands(monkeypatch, tmp_path: Path) -> None:
    loop, bus = _make_loop(monkeypatch, tmp_path)
    outbound = await _roundtrip(loop, bus, "/help")
    assert "/new - 开始新会话并整合记忆" in outbound.content
    assert "/clear - 清空当前会话历史" in outbound.content
    assert "/model <name> - 切换模型" in outbound.content
    assert "/status - 查看运行状态" in outbound.content
    assert "/help - 显示帮助" in outbound.content


async def test_normal_message_gets_response(monkeypatch, tmp_path: Path) -> None:
    loop, bus = _make_loop(monkeypatch, tmp_path, reply="done")
    outbound = await _roundtrip(loop, bus, "hello")
    assert outbound.content == "done"
