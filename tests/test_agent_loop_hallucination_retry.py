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
        self.call_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.call_count += 1
        if self.call_count <= len(self.responses):
            return self.responses[self.call_count - 1]
        return self.responses[-1]

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


async def test_loop_retries_once_on_hallucination_then_uses_tools(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "nanobot.agent.loop.load_config",
        lambda: (_ for _ in ()).throw(ConfigError("test config missing")),
    )
    monkeypatch.setattr("nanobot.agent.loop.supports_function_calling", lambda model: True)

    provider = SequencedProvider(
        [
            LLMResponse(content="我已经执行命令，结果如下：```bash\nls -la\n```"),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    )
                ],
            ),
            LLMResponse(content="已改为真实执行，missing.txt 不存在。"),
        ]
    )

    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="42")

    assert "检测到异常" not in reply
    assert "missing.txt" in reply
    assert provider.call_count == 3
    second_call_messages = provider.calls[1]
    assert any(
        m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and "Execution request detected" in m["content"]
        for m in second_call_messages
    )


async def test_loop_stops_after_one_retry_when_no_tool_calls(monkeypatch, tmp_path: Path) -> None:
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
                    "1. 读取文件\n2. 跑命令\n3. 汇总结果\n"
                    "请确认后我继续。"
                )
            ),
            LLMResponse(
                content=(
                    "继续执行中。我将继续分步处理并回报。\n"
                    "1. 继续检查\n2. 继续验证\n3. 输出结论"
                )
            ),
        ]
    )

    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    reply = await loop.process_direct("开始执行", channel="telegram", chat_id="43")

    assert "连续未调用任何工具" in reply
    assert provider.call_count == 2
