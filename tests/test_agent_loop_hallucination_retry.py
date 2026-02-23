from pathlib import Path
from typing import Any

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.exceptions import ConfigError
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class SequencedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []
        self.tool_choices: list[str] = []
        self.call_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.tool_choices.append(tool_choice)
        self.call_count += 1
        if self.call_count <= len(self.responses):
            return self.responses[self.call_count - 1]
        return self.responses[-1]

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


async def test_execution_intent_without_tools_uses_required_and_stops(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.supports_function_calling", lambda model: True)

    provider = SequencedProvider(
        [
            LLMResponse(
                content=(
                    "已开始执行。下一步我会按以下步骤处理：\n"
                    "1. 读取文件\n2. 运行命令\n3. 汇总结果\n"
                    "如果你同意我将继续。"
                )
            )
        ]
    )
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="43")

    assert "未产生有效工具调用" in reply
    assert provider.call_count == 1
    assert provider.tool_choices == ["required"]


async def test_meaningful_tool_call_switches_later_rounds_to_auto(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.supports_function_calling", lambda model: True)

    provider = SequencedProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    )
                ],
            ),
            LLMResponse(content="已读取文件并完成。"),
        ]
    )
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        assert name == "read_file"
        return "ok"

    monkeypatch.setattr(loop.tools, "execute", _fake_execute)
    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="44")

    assert "已读取文件并完成" in reply
    assert provider.tool_choices == ["required", "auto"]


async def test_required_tool_choice_error_falls_back_to_auto_once(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.supports_function_calling", lambda model: True)

    provider = SequencedProvider(
        [
            LLMResponse(content="Error calling LLM: unsupported tool_choice", finish_reason="error"),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    )
                ],
            ),
            LLMResponse(content="回退后执行成功。"),
        ]
    )
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        assert name == "read_file"
        return "ok"

    monkeypatch.setattr(loop.tools, "execute", _fake_execute)
    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="45")

    assert "回退后执行成功" in reply
    assert provider.tool_choices == ["required", "auto", "auto"]


async def test_non_execution_intent_keeps_tool_choice_auto(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.supports_function_calling", lambda model: True)

    provider = SequencedProvider([LLMResponse(content="这是问答，不需要执行工具。")])
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    reply = await loop.process_direct("为什么这样设计？", channel="telegram", chat_id="46")

    assert "这是问答" in reply
    assert provider.tool_choices == ["auto"]
