import asyncio
import contextvars
import weakref
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import (
    AgentLoop,
    _clean_idle_tool_results,
    _copy_messages_for_microcompact,
    _ToolLoopDetector,
    _is_execution_intent,
    _is_lazy_response,
    _is_meaningful_tool_call,
    _is_stop_signal,
)
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ResultStorageConfig
from nanobot.exceptions import ConfigError
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.session.manager import SessionManager


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
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        return LLMResponse(content=self.reply, finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class ToolThenAnswerProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)
        self.calls = 0
        self.max_tokens_seen: list[int] = []
        self.temperature_seen: list[float] = []
        self.reasoning_effort_seen: list[str | None] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.max_tokens_seen.append(max_tokens)
        self.temperature_seen.append(temperature)
        self.reasoning_effort_seen.append(reasoning_effort)
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="tc-1",
                        name="read_file",
                        arguments={"path": "note.txt"},
                    )
                ],
                finish_reason="tool_calls",
                reasoning_content="先读取文件确认内容。",
                thinking_blocks=[{"type": "thinking", "text": "先读取文件确认内容。"}],
            )
        return LLMResponse(
            content="处理完成",
            finish_reason="stop",
            thinking_blocks=[{"type": "thinking", "text": "读取结果可直接总结。"}],
        )

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class NoToolProvider(LLMProvider):
    def __init__(self, reply: str = "这是正常输出"):
        super().__init__(api_key=None, api_base=None)
        self.reply = reply
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self.reply, finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class CostAwareProvider(LLMProvider):
    def __init__(self) -> None:
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
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="cost-aware reply",
            finish_reason="stop",
            usage={"input_tokens": 100, "output_tokens": 40},
            cache_read_tokens=8,
            cache_creation_tokens=2,
        )

    def get_default_model(self) -> str:
        return "anthropic/claude-3-5-sonnet"


class MessageOnlyProvider(LLMProvider):
    """Always emits message tool calls to simulate notify-only loops."""

    def __init__(self) -> None:
        super().__init__(api_key=None, api_base=None)
        self.calls = 0
        self.tool_choices: list[str] = []
        self.observed_messages: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.tool_choices.append(tool_choice)
        self.observed_messages.append(list(messages))
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id=f"tc-msg-{self.calls}",
                    name="message",
                    arguments={"content": f"任务状态更新 #{self.calls}"},
                )
            ],
            finish_reason="tool_calls",
        )

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class RequiredDowngradeProvider(LLMProvider):
    """First returns exempt tool call, then returns pure text."""

    def __init__(self) -> None:
        super().__init__(api_key=None, api_base=None)
        self.calls = 0
        self.tool_choices: list[str] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.tool_choices.append(tool_choice)
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="tc-msg-1",
                        name="message",
                        arguments={"content": "已完成第一步通知"},
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="最终结论", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class MessageQuotaProvider(LLMProvider):
    """Includes both exempt and meaningful calls so quota path can be exercised."""

    def __init__(self) -> None:
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
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id=f"tc-msg-{self.calls}",
                    name="message",
                    arguments={"content": f"任务状态更新 #{self.calls}"},
                ),
                ToolCallRequest(
                    id=f"tc-read-{self.calls}",
                    name="read_file",
                    arguments={"path": "note.txt"},
                ),
            ],
            finish_reason="tool_calls",
        )

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class MeaningfulThenMessageProvider(LLMProvider):
    """Ensures old meaningful->message exit path still works."""

    def __init__(self) -> None:
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
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="tc-read-1",
                        name="read_file",
                        arguments={"path": "note.txt"},
                    )
                ],
                finish_reason="tool_calls",
            )
        if self.calls == 2:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="tc-msg-2",
                        name="message",
                        arguments={"content": "任务完成通知"},
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="unexpected third call", finish_reason="stop")

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
        reasoning_effort: str | None = None,
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


class ErrorResponseProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content="Error calling LLM: timeout",
            finish_reason="error",
            error_type="timeout",
        )

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class CompactionProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)
        self.calls = 0
        self.observed_messages: list[list[dict[str, Any]]] = []
        self.observed_tool_choices: list[str] = []
        self.observed_tools: list[list[dict[str, Any]] | None] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.observed_messages.append(list(messages))
        self.observed_tool_choices.append(tool_choice)
        self.observed_tools.append(list(tools) if tools is not None else None)
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
        reasoning_effort: str | None = None,
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


class ListArgumentsToolProvider(LLMProvider):
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
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="tc-list-1",
                        name="read_file",
                        arguments=[{"path": "note.txt"}],
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="list arguments ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class MemoryConsolidationListArgumentsProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="tc-memory-list-1",
                    name="memory",
                    arguments=[
                        {
                            "history_entry": "[2026-01-01 10:00] Consolidated list arguments.",
                            "memory_update": "List arguments fallback enabled.",
                        }
                    ],
                )
            ],
            finish_reason="tool_calls",
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


def test_session_compression_lock_uses_weak_container(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    assert isinstance(loop._session_compression_locks, weakref.WeakValueDictionary)


def test_session_compression_lock_reuses_lock_for_same_session(
    monkeypatch, tmp_path: Path
) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    first = loop._get_session_compression_lock("telegram:lock-reuse")
    second = loop._get_session_compression_lock("telegram:lock-reuse")

    assert first is second


def test_session_turn_lock_uses_weak_container(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    assert isinstance(loop._session_turn_locks, weakref.WeakValueDictionary)


async def test_process_direct_serializes_same_session(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    active = 0
    max_active = 0

    async def _fake_process(msg):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(content=msg.content)

    monkeypatch.setattr(loop, "_process_message", _fake_process)

    first, second = await asyncio.gather(
        loop.process_direct("one", session_key="cli:same"),
        loop.process_direct("two", session_key="cli:same"),
    )

    assert {first, second} == {"one", "two"}
    assert max_active == 1


async def test_process_direct_allows_parallel_different_sessions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    active = 0
    max_active = 0

    async def _fake_process(msg):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(content=msg.content)

    monkeypatch.setattr(loop, "_process_message", _fake_process)

    await asyncio.gather(
        loop.process_direct("one", session_key="cli:one"),
        loop.process_direct("two", session_key="cli:two"),
    )

    assert max_active == 2


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


def test_prepare_tool_result_persists_large_output(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    loop.tool_result_max_chars = 9000
    loop.result_storage_config = ResultStorageConfig(threshold_chars=1000, preview_chars=500)

    prepared = loop._prepare_tool_result("X" * 1200, "exec", "call-large")

    assert "Full output saved to workspace path: tool-results/" in prepared
    persisted = list((tmp_path / "tool-results").glob("*.txt"))
    assert len(persisted) == 1
    assert persisted[0].read_text(encoding="utf-8") == "X" * 1200


def test_prepare_tool_result_uses_turn_budget(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    loop.tool_result_max_chars = 9000
    loop.result_storage_config = ResultStorageConfig(
        threshold_chars=4000,
        turn_budget_chars=5000,
        preview_chars=500,
    )
    loop._reset_tool_result_turn_budget()

    first = loop._prepare_tool_result("A" * 3000, "exec", "call-1")
    second = loop._prepare_tool_result("B" * 3000, "exec", "call-2")

    assert first == "A" * 3000
    assert "Full output saved to workspace path: tool-results/" in second
    persisted = list((tmp_path / "tool-results").glob("*.txt"))
    assert len(persisted) == 1
    assert persisted[0].read_text(encoding="utf-8") == "B" * 3000


def test_prepare_tool_result_turn_budget_is_context_local(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    loop.tool_result_max_chars = 9000
    loop.result_storage_config = ResultStorageConfig(
        threshold_chars=4000,
        turn_budget_chars=5000,
        preview_chars=500,
    )

    ctx_one = contextvars.copy_context()
    ctx_two = contextvars.copy_context()
    ctx_one.run(loop._reset_tool_result_turn_budget)
    ctx_two.run(loop._reset_tool_result_turn_budget)
    first = ctx_one.run(loop._prepare_tool_result, "A" * 3000, "exec", "ctx-1-a")
    other = ctx_two.run(loop._prepare_tool_result, "C" * 3000, "exec", "ctx-2-a")
    persisted = ctx_one.run(loop._prepare_tool_result, "B" * 3000, "exec", "ctx-1-b")
    still_inline = ctx_two.run(loop._prepare_tool_result, "D" * 1000, "exec", "ctx-2-b")

    assert first == "A" * 3000
    assert other == "C" * 3000
    assert "Full output saved to workspace path: tool-results/" in persisted
    assert still_inline == "D" * 1000


def test_prepare_tool_result_reports_storage_error(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    loop.result_storage_config = ResultStorageConfig(threshold_chars=1000)

    def _fail_persist(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("nanobot.agent.loop.persist_tool_result_if_needed", _fail_persist)
    prepared = loop._prepare_tool_result("X" * 1200, "exec", "call-fail")

    assert prepared == "Error: tool result storage failed for exec: disk full"


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


async def test_new_keeps_session_when_compression_fails(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    session = loop.sessions.get_or_create("telegram:43")
    session.add_message("user", "one")
    session.add_message("assistant", "two")
    loop.sessions.save(session)

    async def _failed_compress(session_obj):
        return None

    monkeypatch.setattr(loop, "_compress_session_for_new", _failed_compress)
    reply = await loop.process_direct("/new", channel="telegram", chat_id="43")

    assert reply == "记忆整合失败，已保留当前会话。请稍后重试 /new。"
    reloaded = loop.sessions.get_or_create("telegram:43")
    assert len(reloaded.messages) == 2


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


async def test_process_direct_persists_tool_chain_reasoning_effort_and_thinking_blocks(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    provider = ToolThenAnswerProvider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        max_tokens=1234,
        temperature=0.2,
    )

    reply = await loop.process_direct("读取并总结", channel="telegram", chat_id="500")
    assert reply == "处理完成"
    assert provider.max_tokens_seen[0] == 1234
    assert provider.temperature_seen[0] == 0.2
    assert provider.reasoning_effort_seen[0] == "medium"

    session = loop.sessions.get_or_create("telegram:500")
    history = session.get_history()
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert history[1]["reasoning_content"] == "先读取文件确认内容。"
    assert history[1]["thinking_blocks"][0]["type"] == "thinking"
    assert history[2]["tool_call_id"] == "tc-1"
    assert history[2]["name"] == "read_file"
    assert history[3]["thinking_blocks"][0]["text"] == "读取结果可直接总结。"


async def test_process_direct_records_cost_state_and_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = CostAwareProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    reply = await loop.process_direct("track usage", channel="telegram", chat_id="900")
    assert reply == "cost-aware reply"

    session = loop.sessions.get_or_create("telegram:900")
    state = session.metadata["cost_tracker_state"]
    assert state["total_input_tokens"] == 200
    assert state["total_output_tokens"] == 80
    assert state["total_cache_read_tokens"] == 16
    assert state["total_cache_creation_tokens"] == 4
    assert state["total_cost_usd"] > 0
    assert "anthropic/claude-3-5-sonnet" in state["model_usage"]

    status = await loop.process_direct("/status", channel="telegram", chat_id="900")
    assert "- Cost: 200 input, 80 output, 16 cache read, 4 cache write" in status
    assert "anthropic/claude-3-5-sonnet" in status
    assert "$" in status

    reloaded = SessionManager(tmp_path).get_or_create("telegram:900")
    assert reloaded.metadata["cost_tracker_state"] == state


def test_refresh_runtime_options_updates_reasoning_effort(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    assert loop.reasoning_effort == "medium"

    defaults = SimpleNamespace(
        max_tool_iterations=loop.max_iterations,
        max_tokens=loop.max_tokens,
        temperature=loop.temperature,
        reasoning_effort="high",
        idle_intervention=loop.idle_intervention,
        loop_detection_enabled=loop.loop_detection_enabled,
        loop_window=loop.loop_window,
        loop_warn_threshold=loop.loop_warn_threshold,
        loop_critical_threshold=loop.loop_critical_threshold,
        loop_break_threshold=loop.loop_break_threshold,
        max_exempt_rounds=loop.max_exempt_rounds,
        max_message_calls_per_turn=loop.max_message_calls_per_turn,
        model_fallbacks=loop.model_fallbacks,
        failover_retry_once=loop.failover_retry_once,
        context_guard_min_tokens=loop.context_guard_min_tokens,
        context_guard_warn_tokens=loop.context_guard_warn_tokens,
        tool_result_max_chars=loop.tool_result_max_chars,
        compaction_enabled=loop.compaction_enabled,
        compaction_target_ratio=loop.compaction_target_ratio,
    )
    config = SimpleNamespace(agents=SimpleNamespace(defaults=defaults))
    loop._refresh_runtime_options(config)

    assert loop.reasoning_effort == "high"


def test_image_session_persistence_redacts_base64_user_content(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    session = loop.sessions.get_or_create("telegram:image-session")

    base64_payload = "ABCD1234BASE64PAYLOADXYZ"
    multimodal = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_payload}"}},
        {"type": "text", "text": "请保留这段文本"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
    ]

    loop._persist_user_session_message(session, multimodal)
    stored = session.messages[-1]["content"]

    assert isinstance(stored, list)
    assert stored[0] == {"type": "text", "text": "[image]"}
    assert stored[1] == {"type": "text", "text": "请保留这段文本"}
    assert stored[2] == {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    assert base64_payload not in str(stored)


async def test_execution_intent_without_tool_call_no_longer_hard_blocks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = NoToolProvider("我已完成分析并给出结论。")
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, idle_intervention=True)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="501")
    assert "请直接发送可执行指令" not in reply
    assert "检测到你当前请求是执行型任务" not in reply
    assert "我已完成分析并给出结论。" in reply
    assert provider.calls >= 2  # one retry nudge is still expected


async def test_message_only_loop_breaks_within_exempt_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = MessageOnlyProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, idle_intervention=True, max_exempt_rounds=2)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="610")
    assert "任务状态更新 #2" in reply
    assert provider.calls == 2
    assert provider.calls < loop.max_iterations


async def test_required_downgrades_after_exempt_round(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = RequiredDowngradeProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, idle_intervention=True)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="611")
    assert reply == "最终结论"
    assert provider.tool_choices[:2] == ["required", "auto"]
    assert provider.calls == 2


async def test_message_tool_quota_short_circuit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())
    (tmp_path / "note.txt").write_text("quota-check", encoding="utf-8")

    provider = MessageQuotaProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, idle_intervention=True, max_message_calls_per_turn=2, max_exempt_rounds=2)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="612")
    assert "任务状态更新 #3" in reply
    assert provider.calls == 3


async def test_nudge_changes_after_exempt_round(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = MessageOnlyProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, idle_intervention=True, max_exempt_rounds=2)
    await loop.process_direct("开始执行", channel="telegram", chat_id="614")
    assert len(provider.observed_messages) >= 2
    assert provider.observed_messages[1][-1]["role"] == "user"
    assert "如已全部完成，给出最终总结即可" in provider.observed_messages[1][-1]["content"]


def test_loop_detector_tool_name_frequency() -> None:
    detector = _ToolLoopDetector(
        window=10,
        warn_threshold=3,
        critical_threshold=4,
        break_threshold=5,
    )
    signal = None
    for i in range(4):
        signal = detector.observe("message", {"content": f"state-{i}"}, f"result-{i}")
    assert signal is not None
    assert signal.kind == "tool_name_frequency"
    assert signal.count == 4
    assert signal.should_break is False

    signal = detector.observe("message", {"content": "state-5"}, "result-5")
    assert signal is not None
    assert signal.kind == "tool_name_frequency"
    assert signal.count == 5
    assert signal.should_break is True


async def test_meaningful_tool_then_message_still_exits(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    provider = MeaningfulThenMessageProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, idle_intervention=True)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="615")
    assert "任务完成通知" in reply
    assert provider.calls == 2


async def test_process_direct_stop_signal_without_subagents(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    def _fake_cancel(session_key: str) -> int:
        captured["session_key"] = session_key
        return 0

    monkeypatch.setattr(loop.subagents, "cancel_by_session", _fake_cancel)
    reply = await loop.process_direct("停止", channel="telegram", chat_id="101")
    assert captured["session_key"] == "telegram:101"
    assert "已收到停止指令" in reply
    assert "已取消子代理任务 0 个" in reply


async def test_process_direct_stop_signal_with_subagents(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    def _fake_cancel(session_key: str) -> int:
        captured["session_key"] = session_key
        return 2

    monkeypatch.setattr(loop.subagents, "cancel_by_session", _fake_cancel)
    reply = await loop.process_direct("/stop", channel="telegram", chat_id="102")
    assert captured["session_key"] == "telegram:102"
    assert "已收到停止指令" in reply
    assert "已取消子代理任务 2 个" in reply


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


async def test_failover_updates_actual_fallback_provider_env(monkeypatch, tmp_path: Path) -> None:
    from nanobot.config.schema import Config
    from nanobot.providers.codex_provider import CodexProvider

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    codex = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=SimpleNamespace(),
        responses_url="http://localhost:8081/v1/responses",
    )
    fallback = FallbackProvider()

    async def _codex_error(**kwargs):
        return LLMResponse(
            content="Error calling LLM: timeout",
            finish_reason="error",
            error_type="timeout",
        )

    codex._safe_chat = _codex_error  # type: ignore[method-assign]
    config = Config()
    config.providers.anthropic.api_key = "sk-fallback"
    config.providers.anthropic.api_base = "https://relay.example/v1"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=codex,
        fallback_provider=fallback,
        workspace=tmp_path,
        model="gpt-5.3-codex",
    )
    loop.model_fallbacks = ["anthropic/claude-3-5-sonnet"]
    loop.failover_retry_once = False

    response, active_model = await loop._chat_with_failover(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        tool_choice="auto",
        primary_model="gpt-5.3-codex",
        runtime_config=config,
    )

    assert response is not None
    assert response.content == "fallback ok"
    assert active_model == "anthropic/claude-3-5-sonnet"
    assert codex.api_key is None
    assert fallback.api_key == "sk-fallback"
    assert fallback.api_base == "https://relay.example/v1"


def test_runtime_provider_selection_rebuilds_matched_non_codex_provider(
    monkeypatch, tmp_path: Path
) -> None:
    from nanobot.config.schema import Config
    from nanobot.providers.codex_provider import CodexProvider
    from nanobot.providers.litellm_provider import LiteLLMProvider

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    codex = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=SimpleNamespace(),
        responses_url="http://localhost:8081/v1/responses",
    )
    openrouter_fallback = LiteLLMProvider(
        api_key="sk-or-initial",
        default_model="openai/gpt-4o-mini",
        provider_name="openrouter",
    )
    config = Config()
    config.providers.openrouter.api_key = "sk-or-initial"
    config.providers.anthropic.api_key = "sk-ant-runtime"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=codex,
        fallback_provider=openrouter_fallback,
        workspace=tmp_path,
        model="gpt-5.3-codex",
    )
    provider = loop._pick_provider_for_model(
        "anthropic/claude-3-5-sonnet",
        runtime_config=config,
    )

    assert provider is not openrouter_fallback
    assert isinstance(provider, LiteLLMProvider)
    assert provider.api_key == "sk-ant-runtime"
    assert provider._resolve_model("anthropic/claude-3-5-sonnet") == "anthropic/claude-3-5-sonnet"


async def test_error_response_not_persisted_to_session_and_returns_friendly_text(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = ErrorResponseProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    loop.model_fallbacks = []
    loop.failover_retry_once = False

    out = await loop._process_message(
        InboundMessage(
            channel="telegram",
            sender_id="u-error",
            chat_id="103",
            content="what is 1+1?",
        )
    )

    assert out is not None
    assert out.content == "抱歉，模型服务暂时不可用，请稍后再试。"
    session = loop.sessions.get_or_create("telegram:103")
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "what is 1+1?"


def test_context_guard_prunes_large_messages(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    messages = [{"role": "system", "content": "sys"}]
    for i in range(40):
        messages.append({"role": "user", "content": f"{i} " + ("x" * 5000)})
    trimmed = loop._guard_context_window(messages, "qwen-long-context")
    assert len(trimmed) < len(messages)


def test_sanitize_orphan_tools_fast(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "ok"},
        {"role": "tool", "tool_call_id": "call_orphan", "name": "read_file", "content": "bad"},
    ]

    filtered = loop._sanitize_orphan_tools_fast(messages)
    tool_ids = [msg.get("tool_call_id") for msg in filtered if msg.get("role") == "tool"]
    assert tool_ids == ["call_1"]


def test_trim_messages_drops_partial_tool_chain(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-chain-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-chain-1",
            "name": "read_file",
            "content": "result " + ("x" * 6000),
        },
        {"role": "user", "content": "继续执行"},
    ]
    budget = loop._estimate_messages_tokens([messages[0], messages[-1]]) + 32
    trimmed = loop._trim_messages_to_budget(messages, budget)

    tool_ids = {str(m.get("tool_call_id")) for m in trimmed if m.get("role") == "tool"}
    assert "tc-chain-1" not in tool_ids
    assert trimmed[-1]["role"] == "user"


def test_trim_messages_keeps_latest_tool_chain_atomic(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-chain-2",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-chain-2",
            "name": "read_file",
            "content": "result " + ("y" * 9000),
        },
    ]
    budget = loop._estimate_messages_tokens([messages[0]]) + 32
    trimmed = loop._trim_messages_to_budget(messages, budget)

    assistant_call_ids = {
        tc.get("id")
        for msg in trimmed
        if msg.get("role") == "assistant"
        for tc in (msg.get("tool_calls") or [])
        if isinstance(tc, dict)
    }
    tool_ids = {msg.get("tool_call_id") for msg in trimmed if msg.get("role") == "tool"}
    assert tool_ids
    assert tool_ids.issubset(assistant_call_ids)


def test_align_recent_start_index_moves_to_assistant_origin(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    non_system = [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-origin",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-origin", "content": "ok", "name": "list_dir"},
        {"role": "user", "content": "u2"},
    ]
    assert loop._align_recent_start_index(non_system, 2) == 1


def test_align_recent_start_index_skips_orphan_tool(monkeypatch, tmp_path: Path) -> None:
    loop = _make_loop(monkeypatch, tmp_path)
    non_system = [
        {"role": "tool", "tool_call_id": "missing", "content": "orphan", "name": "read_file"},
        {"role": "tool", "tool_call_id": "missing", "content": "orphan2", "name": "read_file"},
        {"role": "user", "content": "继续"},
    ]
    assert loop._align_recent_start_index(non_system, 0) == 2


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
        isinstance(msg.get("content"), str)
        and ContextBuilder._SYSTEM_REMINDER_TAG in msg.get("content", "")
        and "压缩摘要" in msg.get("content", "")
        for msg in compacted
    )
    assert provider.calls >= 1
    assert provider.observed_tool_choices[0] == "none"
    assert provider.observed_tools[0]
    assert provider.observed_messages[0][0]["content"] == "system prompt"


def test_query_microcompact_does_not_mutate_source_messages() -> None:
    messages = [
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "read_file", "content": "x" * 5000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t2", "name": "read_file", "content": "y" * 5000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t3", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t3", "name": "read_file", "content": "z" * 5000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t4", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t4", "name": "read_file", "content": "w" * 5000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t5", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t5", "name": "read_file", "content": "v" * 5000},
    ]

    view = _copy_messages_for_microcompact(messages)

    assert view is not messages
    assert view[2]["content"] == "[Old tool result content cleared]"
    assert messages[2]["content"] == "x" * 5000


def test_idle_tool_result_cleanup_clears_stale_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    session = SessionManager(tmp_path).get_or_create("telegram:idle")
    old_ts = (datetime.now() - timedelta(minutes=31)).isoformat()
    session.messages = [
        {"role": "user", "content": "start", "timestamp": old_ts},
        {"role": "assistant", "content": "done", "timestamp": old_ts},
        {"role": "tool", "tool_call_id": "t1", "name": "read_file", "content": "x" * 5000, "timestamp": old_ts},
        {"role": "assistant", "content": "done2", "timestamp": old_ts},
        {"role": "tool", "tool_call_id": "t2", "name": "read_file", "content": "y" * 5000, "timestamp": old_ts},
        {"role": "assistant", "content": "done3", "timestamp": old_ts},
        {"role": "tool", "tool_call_id": "t3", "name": "read_file", "content": "z" * 5000, "timestamp": old_ts},
        {"role": "assistant", "content": "done4", "timestamp": old_ts},
        {"role": "tool", "tool_call_id": "t4", "name": "read_file", "content": "w" * 5000, "timestamp": old_ts},
        {"role": "assistant", "content": "done5", "timestamp": old_ts},
        {"role": "tool", "tool_call_id": "t5", "name": "read_file", "content": "v" * 5000, "timestamp": old_ts},
    ]

    changed = _clean_idle_tool_results(session)

    assert changed is True
    assert session.messages[2]["content"] == "[Old tool result content cleared]"


async def test_compaction_failure_breaker_skips_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = NoToolProvider(reply="ok")
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    session = loop.sessions.get_or_create("telegram:breaker")
    session.metadata["compaction_failure_streak"] = 3
    session.add_message("user", "history " + ("x" * 12000))
    session.add_message("assistant", "reply " + ("y" * 12000))
    loop.sessions.save(session)

    called = False

    async def _boom(*args, **kwargs):
        raise AssertionError("compaction helper should be skipped")

    def _guard(messages, model):
        nonlocal called
        called = True
        return messages

    monkeypatch.setattr(loop, "_compact_messages_for_context", _boom)
    monkeypatch.setattr(loop, "_guard_context_window", _guard)

    reply = await loop.process_direct("继续", channel="telegram", chat_id="breaker")

    assert reply == "ok"
    assert called is True


async def test_resumed_session_restores_persisted_runtime_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    loop = AgentLoop(bus=MessageBus(), provider=CostAwareProvider(), workspace=tmp_path)
    session = loop.sessions.get_or_create("telegram:restore")
    session.metadata.update(
        {
            "compaction_failure_streak": "2",
            "last_assistant_timestamp": "",
            "cost_tracker_state": {
                "total_input_tokens": 100,
                "total_output_tokens": 40,
                "total_cache_read_tokens": 8,
                "total_cache_creation_tokens": 2,
                "total_cost_usd": 0.0003,
                "model_usage": {
                    "anthropic/claude-3-5-sonnet": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_read_tokens": 8,
                        "cache_creation_tokens": 2,
                        "cost_usd": 0.0003,
                    }
                },
            },
            "mode": 1,
            "worker_summary": {"owner": "worker-g"},
        }
    )
    session.add_message("assistant", "previous turn", timestamp="2026-03-31T09:00:00")
    loop.sessions.save(session)

    reply = await loop.process_direct("继续", channel="telegram", chat_id="restore")

    assert reply == "cost-aware reply"
    restored = loop.sessions.get_or_create("telegram:restore")
    assert restored.metadata["compaction_failure_streak"] == 0
    assert restored.metadata["mode"] == "1"
    assert restored.metadata["worker_summary"] == "{'owner': 'worker-g'}"
    assert restored.metadata["last_assistant_timestamp"]

    cost_state = restored.metadata["cost_tracker_state"]
    assert cost_state["total_input_tokens"] == 300
    assert cost_state["total_output_tokens"] == 120
    assert cost_state["total_cache_read_tokens"] == 24
    assert cost_state["total_cache_creation_tokens"] == 6
    assert cost_state["model_usage"]["anthropic/claude-3-5-sonnet"]["input_tokens"] == 300


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


async def test_compaction_fork_appends_instruction_to_parent_prefix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    provider = CompactionProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    parent_messages = [
        {"role": "system", "content": "stable system"},
        {"role": "user", "content": "history one"},
        {"role": "assistant", "content": "history two"},
    ]

    summary, used_model = await loop._summarize_compaction_text(
        source_text="old context",
        active_model="openai/gpt-4o-mini",
        parent_messages=parent_messages,
        tools=loop.tools.get_definitions(),
    )

    assert summary is not None
    assert used_model == "openai/gpt-4o-mini"
    sent = provider.observed_messages[0]
    assert sent[:-1] == parent_messages
    assert ContextBuilder._SYSTEM_REMINDER_TAG in sent[-1]["content"]
    assert "不要调用任何工具" in sent[-1]["content"]


async def test_empty_assistant_without_tool_calls_not_persisted_to_session(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    loop = AgentLoop(bus=MessageBus(), provider=NoToolProvider(reply=""), workspace=tmp_path)
    msg = InboundMessage(
        channel="system",
        sender_id="subagent-empty",
        chat_id="telegram:88",
        content="continue",
    )

    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == ""
    session = loop.sessions.get_or_create("telegram:88")
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "[System: subagent-empty] continue"


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


async def test_arguments_list_tool_call_does_not_crash_and_continues(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    (tmp_path / "note.txt").write_text("list args content", encoding="utf-8")
    provider = ListArgumentsToolProvider()
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    reply = await loop.process_direct("读取文件并继续", channel="telegram", chat_id="701")

    assert reply == "list arguments ok"
    assert provider.calls == 2
    history = loop.sessions.get_or_create("telegram:701").get_history()
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]


async def test_arguments_list_memory_consolidation_uses_first_item(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())

    loop = AgentLoop(
        bus=MessageBus(),
        provider=MemoryConsolidationListArgumentsProvider(),
        workspace=tmp_path,
    )
    session = loop.sessions.get_or_create("telegram:memory-list")
    session.add_message("user", "请整理历史")
    session.add_message("assistant", "好的")
    loop.sessions.save(session)

    result = await loop._consolidate_memory(session, archive_all=True)

    assert result is not None
    assert result["success"] is True
    assert result["history_added"] is True
    assert result["memory_updated"] is True
    assert session.messages == []
