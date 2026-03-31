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


async def test_spawn_registers_worker_protocol_record(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )

    gate = asyncio.Event()
    seen: dict[str, Any] = {}

    async def _blocking_run(record: Any, task: str, origin: dict[str, str]) -> None:
        seen["record"] = record
        seen["task"] = task
        seen["origin"] = origin
        await gate.wait()

    monkeypatch.setattr(manager, "_run_subagent", _blocking_run)

    message = await manager.spawn(
        task="protocol-task",
        label="Protocol Task",
        origin_channel="cli",
        origin_chat_id="chat-a",
    )

    assert "worker:" in message
    await _tick()
    assert len(manager._worker_records) == 1
    record = seen["record"]
    assert record.worker_id.startswith("worker-")
    assert record.label == "Protocol Task"
    assert record.session_key == "cli:chat-a"
    assert seen["task"] == "protocol-task"
    assert seen["origin"] == {"channel": "cli", "chat_id": "chat-a"}
    assert manager._worker_records[record.worker_id] == record
    assert record.task_id in manager._running_tasks

    manager._running_tasks[record.task_id].cancel()
    await _tick()
    assert len(manager._worker_records) == 0


async def test_continue_unknown_worker_returns_safe_error(
    tmp_path: Path,
) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )

    result = await manager.continue_worker("worker-missing", "follow-up task")

    assert result.startswith("Error: ")
    assert "worker-missing" not in manager._worker_mailboxes


async def test_continue_existing_worker_reuses_worker_id_and_records_mailbox(
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

    start_message = await manager.spawn(
        task="initial task",
        label="Initial Task",
        origin_channel="cli",
        origin_chat_id="chat-a",
    )
    assert "worker:" in start_message
    await _tick()

    assert len(manager._worker_mailboxes) == 1
    worker_id = next(iter(manager._worker_mailboxes))
    mailbox = manager._worker_mailboxes[worker_id]
    assert mailbox.session_key == "cli:chat-a"
    assert mailbox.origin_channel == "cli"
    assert mailbox.origin_chat_id == "chat-a"
    assert len(mailbox.history) == 1
    assert mailbox.history[0].kind == "spawn"
    assert mailbox.history[0].task == "initial task"

    gate = asyncio.Event()
    seen: dict[str, Any] = {}

    async def _blocking_run(record: Any, task: str, origin: dict[str, str]) -> None:
        seen["record"] = record
        seen["task"] = task
        seen["origin"] = origin
        await gate.wait()

    monkeypatch.setattr(manager, "_run_subagent", _blocking_run)

    result = await manager.continue_worker(worker_id, "follow-up task")
    assert "continued" in result
    await _tick()

    assert len(manager._worker_records) == 1
    record = seen["record"]
    assert record.worker_id == worker_id
    assert record.session_key == "cli:chat-a"
    assert seen["task"] == "follow-up task"
    assert seen["origin"] == {"channel": "cli", "chat_id": "chat-a"}
    assert len(manager._worker_mailboxes[worker_id].history) == 2
    assert manager._worker_mailboxes[worker_id].history[1].kind == "continue"
    assert manager._worker_mailboxes[worker_id].history[1].task == "follow-up task"

    gate.set()
    await _tick()
    assert len(manager._worker_records) == 0


async def test_task_notification_contains_worker_id_and_result(
    tmp_path: Path,
) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )

    record_type = type(
        "Record",
        (),
        {
            "worker_id": "worker-123",
            "task_id": "task-123",
            "label": "Protocol Task",
        },
    )
    notification = manager._build_task_notification(
        record_type(),
        "completed",
        "Agent 'Protocol Task' completed successfully",
        "ok",
    )

    assert "<task-notification>" in notification
    assert "<task-id>worker-123</task-id>" in notification
    assert "<status>completed</status>" in notification
    assert "<summary>Agent 'Protocol Task' completed successfully</summary>" in notification
    assert "<result>ok</result>" in notification
