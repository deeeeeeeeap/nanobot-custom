from types import SimpleNamespace

from nanobot.providers.litellm_provider import LiteLLMProvider


async def test_provider_chat_returns_error_response_on_runtime_failure(monkeypatch) -> None:
    async def _boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("nanobot.providers.litellm_provider.acompletion", _boom)

    provider = LiteLLMProvider(api_key="k", default_model="openai/gpt-4o-mini")
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert resp.content is not None
    assert "Error calling LLM:" in resp.content


def test_provider_gateway_prefix_resolution() -> None:
    provider = LiteLLMProvider(
        api_key="k",
        provider_name="openrouter",
        default_model="anthropic/claude-3-5-sonnet",
    )
    resolved = provider._resolve_model("anthropic/claude-3-5-sonnet")
    assert resolved.startswith("openrouter/")


def test_provider_parse_response_without_tool_calls() -> None:
    provider = LiteLLMProvider(api_key="k")
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )

    parsed = provider._parse_response(fake_response)
    assert parsed.content == "ok"
    assert parsed.tool_calls == []
    assert parsed.usage["total_tokens"] == 3
