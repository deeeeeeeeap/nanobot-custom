from pathlib import Path
from typing import Any

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.exceptions import ConfigError
from nanobot.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key=None, api_base=None)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content="ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-4o-mini"


async def test_agent_loop_process_direct_tolerates_config_reload_errors(monkeypatch, tmp_path: Path) -> None:
    def _bad_load_config():
        raise ConfigError("broken config")

    class _Hallucination:
        is_hallucination = False
        pattern_name = ""
        confidence = 0.0

    monkeypatch.setattr("nanobot.config.loader.load_config", _bad_load_config)
    monkeypatch.setattr("nanobot.agent.loop.detect_hallucination", lambda *a, **k: _Hallucination())
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
    )

    result = await loop.process_direct("hello")
    assert result
