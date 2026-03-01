import asyncio
from pathlib import Path
from typing import Any

from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    def __init__(self) -> None:
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
        return LLMResponse(content="ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


async def _tick() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_cancel_by_session_cancels_only_target_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )

    gate = asyncio.Event()

    async def _blocking_run(*args: Any, **kwargs: Any) -> None:
        await gate.wait()

    monkeypatch.setattr(manager, "_run_subagent", _blocking_run)

    await manager.spawn(task="task-a1", origin_channel="cli", origin_chat_id="chat-a")
    await manager.spawn(task="task-a2", origin_channel="cli", origin_chat_id="chat-a")
    await manager.spawn(task="task-b1", origin_channel="cli", origin_chat_id="chat-b")

    assert manager.get_running_count() == 3

    cancelled = manager.cancel_by_session("cli:chat-a")
    assert cancelled == 2
    await _tick()

    assert manager.get_running_count() == 1
    assert "cli:chat-a" not in manager._session_task_ids
    assert manager.cancel_by_session("cli:missing") == 0

    assert manager.cancel_by_session("cli:chat-b") == 1
    await _tick()
    assert manager.get_running_count() == 0
    assert len(manager._session_task_ids) == 0


async def test_cancel_by_session_respects_explicit_session_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )

    gate = asyncio.Event()

    async def _blocking_run(*args: Any, **kwargs: Any) -> None:
        await gate.wait()

    monkeypatch.setattr(manager, "_run_subagent", _blocking_run)

    await manager.spawn(
        task="scheduled-task",
        origin_channel="cli",
        origin_chat_id="chat-a",
        session_key="cron:job-1",
    )

    assert manager.cancel_by_session("cli:chat-a") == 0
    assert manager.cancel_by_session("cron:job-1") == 1
    await _tick()
    assert manager.get_running_count() == 0
    assert len(manager._session_task_ids) == 0


async def test_finished_task_is_removed_from_session_tracking(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )

    async def _fast_run(*args: Any, **kwargs: Any) -> None:
        return

    monkeypatch.setattr(manager, "_run_subagent", _fast_run)

    await manager.spawn(task="quick-task", origin_channel="cli", origin_chat_id="chat-a")
    await _tick()

    assert manager.get_running_count() == 0
    assert manager.cancel_by_session("cli:chat-a") == 0
    assert len(manager._session_task_ids) == 0



