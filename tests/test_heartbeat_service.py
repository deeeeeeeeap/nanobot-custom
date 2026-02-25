from pathlib import Path
from typing import Any

from nanobot.heartbeat.service import HeartbeatService
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _HeartbeatProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self._responses = list(responses)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


def _decision(action: str, tasks: str = "") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(
                id="hb-1",
                name="heartbeat",
                arguments={"action": action, "tasks": tasks},
            )
        ],
        finish_reason="tool_calls",
    )


async def test_heartbeat_tick_skips_when_decision_is_skip(tmp_path: Path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("check schedule", encoding="utf-8")
    provider = _HeartbeatProvider([_decision("skip")])
    executed = {"value": False}

    async def _execute(tasks: str) -> str:
        executed["value"] = True
        return "ok"

    heartbeat = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="openai/gpt-4o-mini",
        on_execute=_execute,
    )
    await heartbeat._tick()
    assert executed["value"] is False


async def test_heartbeat_tick_runs_and_notifies(tmp_path: Path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("daily briefing", encoding="utf-8")
    provider = _HeartbeatProvider([_decision("run", "run daily briefing now")])
    seen: dict[str, str] = {}

    async def _execute(tasks: str) -> str:
        seen["tasks"] = tasks
        return "done"

    async def _notify(response: str) -> None:
        seen["response"] = response

    heartbeat = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="openai/gpt-4o-mini",
        on_execute=_execute,
        on_notify=_notify,
    )
    await heartbeat._tick()
    assert seen["tasks"] == "run daily briefing now"
    assert seen["response"] == "done"


async def test_heartbeat_trigger_now_returns_execution_result(tmp_path: Path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("ping", encoding="utf-8")
    provider = _HeartbeatProvider([_decision("run", "ping task")])

    async def _execute(tasks: str) -> str:
        return f"executed:{tasks}"

    heartbeat = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="openai/gpt-4o-mini",
        on_execute=_execute,
    )
    result = await heartbeat.trigger_now()
    assert result == "executed:ping task"


async def test_heartbeat_start_is_idempotent(tmp_path: Path) -> None:
    provider = _HeartbeatProvider([])
    heartbeat = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="openai/gpt-4o-mini",
        interval_s=3600,
    )
    await heartbeat.start()
    first_task = heartbeat._task
    await heartbeat.start()
    assert heartbeat._task is first_task
    heartbeat.stop()
