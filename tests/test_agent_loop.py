import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nanobot.agent.loop import (
    AgentLoop,
    _ToolLoopDetector,
    _is_execution_intent,
    _is_lazy_response,
    _is_meaningful_tool_call,
    _is_stop_signal,
)
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.exceptions import ConfigError
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


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


class FallbackProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)
        self.models: list[str] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        target = model or "openai/gpt-4o-mini"
        self.models.append(target)
        if "openai" in target:
            return LLMResponse(
                content="Error calling LLM: timeout",
                finish_reason="error",
                error_type="timeout",
            )
        return LLMResponse(content="fallback ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class CompactionProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content="压缩摘要：用户要求修复脚本并验证输出。", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class SystemLoopProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id=f"tc-{self.calls}",
                    name="read_file",
                    arguments={"path": "README.md"},
                )
            ],
        )

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


def test_execution_intent_excludes_smalltalk() -> None:
    assert _is_execution_intent("hello") is False
    assert _is_execution_intent("你好") is False
    assert _is_execution_intent("谢谢") is False


def test_lazy_detects_admitted_inaction() -> None:
    """模型承认自己还没做正事的典型空转模式。"""
    content = (
        "进展复盘：\n"
        "- ✅ 已发执行中通知给你\n"
        "- ❌ 还没做你要求的核心动作\n"
        "下一步我将直接执行，不再停顿：\n"
        "1. 提取段落到归档\n"
        "2. 从 MEMORY.md 删除\n"
        "3. 发完成通知"
    )
    assert _is_lazy_response(content) is True


def test_idle_exempt_tools() -> None:
    """message/send_message 不算有意义的工具调用。"""
    from nanobot.agent.loop import _IDLE_EXEMPT_TOOLS
    assert "message" in _IDLE_EXEMPT_TOOLS
    assert "send_message" in _IDLE_EXEMPT_TOOLS
    assert "exec" not in _IDLE_EXEMPT_TOOLS
    assert "read_file" not in _IDLE_EXEMPT_TOOLS


def test_meaningful_tool_call_for_exec_trivial_vs_real() -> None:
    assert _is_meaningful_tool_call("message", {"content": "ping"}) is False
    assert _is_meaningful_tool_call("exec", {"command": "whoami"}) is False
    assert _is_meaningful_tool_call("exec", {"command": "date"}) is False
    assert _is_meaningful_tool_call("exec", {"command": "ls -la"}) is True


def test_stop_signal_supports_multilingual_variants() -> None:
    assert _is_stop_signal("/stop") is True
    assert _is_stop_signal("停止") is True
    assert _is_stop_signal("取消") is True
    assert _is_stop_signal("cancel") is True
    assert _is_stop_signal("please continue") is False


def test_tool_loop_detector_breaks_on_repeated_calls() -> None:
    detector = _ToolLoopDetector(
        window=30,
        warn_threshold=8,
        critical_threshold=12,
        break_threshold=18,
    )
    signal = None
    for _ in range(18):
        signal = detector.observe("read_file", {"path": "README.md"}, "same content")
    assert signal is not None
    assert signal.kind in {"generic_repeat", "known_poll_no_progress"}
    assert signal.should_break is True


def test_poll_no_progress_count_excludes_latest_call_itself() -> None:
    detector = _ToolLoopDetector(
        window=30,
        warn_threshold=8,
        critical_threshold=12,
        break_threshold=18,
    )
    for _ in range(18):
        detector.observe("read_file", {"path": "README.md"}, "same content")
    signal = detector._check_poll_no_progress()
    assert signal is not None
    assert signal.count == 17


def test_tool_result_budget_keeps_head_and_tail(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    loop.tool_result_max_chars = 9000
    source = "A" * 5000 + "MIDDLE" * 200 + "B" * 3000
    reduced = loop._truncate_tool_result(source)
    assert reduced.startswith("A" * 100)
    assert "省略" in reduced
    assert reduced.endswith("B" * 100)


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


async def test_process_direct_stop_signal(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    reply = await loop.process_direct("停止", channel="telegram", chat_id="101")
    assert "已收到停止指令" in reply


async def test_failover_switches_to_fallback_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = FallbackProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    loop.model = "openai/gpt-4o-mini"
    loop.model_fallbacks = ["anthropic/claude-3-5-sonnet"]
    loop.failover_retry_once = False

    reply = await loop.process_direct("what is 1+1?", channel="telegram", chat_id="102")
    assert reply == "fallback ok"
    assert provider.models[0] == "openai/gpt-4o-mini"
    assert provider.models[1] == "anthropic/claude-3-5-sonnet"


def test_context_guard_prunes_large_messages(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    messages = [{"role": "system", "content": "sys"}]
    for i in range(40):
        messages.append({"role": "user", "content": f"{i} " + ("x" * 5000)})
    trimmed = loop._guard_context_window(messages, "qwen-long-context")
    assert len(trimmed) < len(messages)


def test_classify_response_error_not_overmatching_format(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    generic = SimpleNamespace(content="unsupported format image/webp", error_type=None)
    precise = SimpleNamespace(content="invalid json schema for tool arguments", error_type=None)
    assert loop._classify_response_error(generic) == "unknown"
    assert loop._classify_response_error(precise) == "format"


async def test_compaction_summarizes_old_messages(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = CompactionProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    messages = [{"role": "system", "content": "system prompt"}]
    for i in range(45):
        messages.append({"role": "user", "content": f"user-{i} " + ("x" * 4000)})
        messages.append({"role": "assistant", "content": f"assistant-{i} " + ("y" * 4000)})

    compacted, _ = await loop._compact_messages_for_context(messages, "qwen-mini")
    assert len(compacted) < len(messages)
    assert any(
        isinstance(msg.get("content"), str) and "会话压缩摘要" in msg.get("content", "")
        for msg in compacted
    )
    assert provider.calls >= 1


async def test_compaction_summary_timeout_falls_back(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)

    async def _fake_chat_with_failover(**kwargs):
        await asyncio.sleep(0)
        return LLMResponse(content="should not be used"), "openai/gpt-4o-mini"

    async def _timeout(coro, timeout):
        if hasattr(coro, "close"):
            coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(loop, "_chat_with_failover", _fake_chat_with_failover)
    monkeypatch.setattr("nanobot.agent.loop.asyncio.wait_for", _timeout)

    summary, model = await loop._summarize_compaction_text(
        source_text="history",
        active_model="openai/gpt-4o-mini",
    )
    assert summary is None
    assert model == "openai/gpt-4o-mini"


async def test_system_message_path_uses_loop_detector(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = SystemLoopProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    loop.loop_warn_threshold = 2
    loop.loop_critical_threshold = 3
    loop.loop_break_threshold = 4

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        return "same result"

    monkeypatch.setattr(loop.tools, "execute", _fake_execute)
    msg = InboundMessage(
        channel="system",
        sender_id="subagent-1",
        chat_id="telegram:42",
        content="continue",
    )
    out = await loop._process_message(msg)
    assert out is not None
    assert "检测到工具调用可能进入死循环" in out.content
