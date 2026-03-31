from typing import Any

from nanobot.agent.tools.spawn import SpawnTool


class FakeManager:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, Any]] = []
        self.continue_calls: list[dict[str, Any]] = []

    async def spawn(self, **kwargs: Any) -> str:
        self.spawn_calls.append(kwargs)
        return "spawn-called"

    async def continue_worker(self, **kwargs: Any) -> str:
        self.continue_calls.append(kwargs)
        return "continue-called"

async def test_spawn_tool_routes_worker_id_to_continue_worker() -> None:
    manager = FakeManager()
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    tool.set_context("cli", "chat-a")

    result = await tool.execute(
        task="follow-up task",
        label="Follow Up",
        worker_id="worker-123",
        session_key="ignored",
    )

    assert result == "continue-called"
    assert manager.spawn_calls == []
    assert manager.continue_calls == [
        {
            "worker_id": "worker-123",
            "task": "follow-up task",
            "label": "Follow Up",
        }
    ]


async def test_spawn_tool_routes_new_worker_to_spawn() -> None:
    manager = FakeManager()
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    tool.set_context("cli", "chat-a")

    result = await tool.execute(
        task="new task",
        label="New Task",
        session_key="session-1",
    )

    assert result == "spawn-called"
    assert manager.continue_calls == []
    assert manager.spawn_calls == [
        {
            "task": "new task",
            "label": "New Task",
            "origin_channel": "cli",
            "origin_chat_id": "chat-a",
            "session_key": "session-1",
        }
    ]
