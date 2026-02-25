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
        self.tool_choices.append(tool_choice)
        self.call_count += 1
        if self.call_count <= len(self.responses):
            return self.responses[self.call_count - 1]
        return self.responses[-1]

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


class _NoHallucination:
    is_hallucination = False
    pattern_name = ""
    confidence = 0.0


def _prepare(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.supports_function_calling", lambda model: True)
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _NoHallucination())


async def test_execution_intent_retries_once_then_uses_tools(monkeypatch, tmp_path: Path) -> None:
    _prepare(monkeypatch, tmp_path)

    provider = SequencedProvider(
        [
            LLMResponse(content="我会开始执行，下一步处理文件并汇总结果。"),
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
            LLMResponse(content="已执行完成。"),
        ]
    )
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        assert name == "read_file"
        return "ok"

    monkeypatch.setattr(loop.tools, "execute", _fake_execute)
    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="43")

    assert "已执行完成" in reply
    assert provider.tool_choices == ["required", "required", "auto"]


async def test_execution_intent_stops_after_single_internal_retry(
    monkeypatch, tmp_path: Path
) -> None:
    """反空转降压后，模型不再被硬阻断，而是允许正常输出。"""
    _prepare(monkeypatch, tmp_path)

    provider = SequencedProvider(
        [
            LLMResponse(content="我会继续执行。"),
            LLMResponse(content="继续执行中。"),
        ]
    )
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="44")

    # 降压后允许模型正常输出，不再硬阻断
    assert reply  # 有回复内容
    assert provider.call_count == 2
    assert provider.tool_choices == ["required", "required"]


async def test_required_tool_choice_error_falls_back_to_auto_once(
    monkeypatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch, tmp_path)

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
    _prepare(monkeypatch, tmp_path)

    provider = SequencedProvider([LLMResponse(content="这是问答，不需要执行工具。")])
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    reply = await loop.process_direct("为什么这样设计？", channel="telegram", chat_id="46")

    assert "这是问答" in reply
    assert provider.tool_choices == ["auto"]


async def test_repeated_failure_escalates_user_message(monkeypatch, tmp_path: Path) -> None:
    """降压后连续未调工具不再硬阻断，而是允许正常输出并记录 streak。"""
    _prepare(monkeypatch, tmp_path)

    provider = SequencedProvider(
        [
            LLMResponse(content="我会先准备。"),
            LLMResponse(content="继续准备。"),
            LLMResponse(content="我会继续处理。"),
            LLMResponse(content="继续处理中。"),
        ]
    )
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    first = await loop.process_direct("开始执行", channel="telegram", chat_id="47")
    second = await loop.process_direct("开始执行", channel="telegram", chat_id="47")

    # 降压后允许模型正常输出
    assert first
    assert second
