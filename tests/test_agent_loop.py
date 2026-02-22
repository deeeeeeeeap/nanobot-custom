import asyncio
from pathlib import Path
from typing import Any

from nanobot.agent.loop import AgentLoop, _is_execution_intent, _is_lazy_response
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


def _make_loop(monkeypatch, tmp_path: Path) -> AgentLoop:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())
    return AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
    )


def test_lazy_detects_empty_promises() -> None:
    content = (
        "Please wait while I prepare everything for execution. "
        "I will continue once you confirm, and I will keep the plan explicit.\n"
        "1. Next step: inspect files.\n"
        "2. Next step: run commands.\n"
        "3. Next step: summarize results.\n"
        + " details" * 40
    )
    assert _is_lazy_response(content) is True


def test_lazy_detects_planning_language() -> None:
    content = (
        "Please wait. Next step is collecting the repository context before any final answer.\n"
        "1. Next step: scan modules.\n"
        "2. Next step: inspect tests.\n"
        "3. Next step: produce a plan."
    )
    assert _is_lazy_response(content) is True


def test_lazy_detects_english_if_you_agree_pattern() -> None:
    content = (
        "If you agree, I will proceed with these steps right now.\n"
        "1. Next step: inspect files.\n"
        "2. Next step: run command.\n"
        "3. Next step: summarize."
    )
    assert _is_lazy_response(content) is True


def test_lazy_ignores_normal_response() -> None:
    content = "Implemented the change and verified tests are now passing."
    assert _is_lazy_response(content) is False


def test_lazy_score_threshold() -> None:
    content = (
        "I will provide a detailed explanation of what changed and why. "
        + "long_context " * 80
    )
    assert _is_lazy_response(content) is False


def test_execution_intent_defaults_to_execute_for_imperative_text() -> None:
    assert _is_execution_intent("帮我部署一下") is True
    assert _is_execution_intent("把这个改了") is True
    assert _is_execution_intent("跑个测试") is True


def test_execution_intent_excludes_obvious_questions() -> None:
    assert _is_execution_intent("为什么这样设计？") is False
    assert _is_execution_intent("这两个方案什么区别") is False
    assert _is_execution_intent("If you agree, what is the difference?") is False


async def test_new_command_returns_feedback(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    session = loop.sessions.get_or_create("telegram:42")
    session.add_message("user", "hello")
    session.add_message("assistant", "world")
    loop.sessions.save(session)

    class _Result:
        created = 1
        merged = 1
        skipped = 0
        summary = "ok"

    async def _ok_compress(session_obj):
        return _Result()

    monkeypatch.setattr(loop, "_compress_session_for_new", _ok_compress)

    reply = await loop.process_direct("/new", channel="telegram", chat_id="42")
    assert "已开始新会话。" in reply
    assert "- 已归档消息数: 2" in reply
    assert "created=1, merged=1, skipped=0" in reply
    assert "- 历史记录: 已写入 HISTORY.md" in reply


async def test_new_empty_session_feedback(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)

    async def _no_consolidation(session_obj):
        return None

    monkeypatch.setattr(loop, "_compress_session_for_new", _no_consolidation)

    reply = await loop.process_direct("/new", channel="telegram", chat_id="empty")
    assert reply == "已开始新会话（原会话本来就是空的）。"


async def test_process_direct_uses_explicit_session_key(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)

    await loop.process_direct(
        "first task",
        session_key="cron:job-1",
        channel="telegram",
        chat_id="42",
    )
    await loop.process_direct(
        "second task",
        session_key="cron:job-2",
        channel="telegram",
        chat_id="42",
    )

    session_1 = loop.sessions.get_or_create("cron:job-1")
    session_2 = loop.sessions.get_or_create("cron:job-2")
    default_chat_session = loop.sessions.get_or_create("telegram:42")

    assert len(session_1.messages) == 2
    assert len(session_2.messages) == 2
    assert default_chat_session.messages == []


async def test_auto_compress_background_trigger(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    loop.memory_config.compress_threshold = 2
    loop.memory_config.auto_compress = True

    called = {"value": False}

    async def _fake_bg(session_key: str, message_count: int) -> None:
        called["value"] = True

    monkeypatch.setattr(loop, "_compress_session_background", _fake_bg)
    await loop.process_direct("trigger", channel="telegram", chat_id="99")
    await asyncio.sleep(0)
    assert called["value"] is True
