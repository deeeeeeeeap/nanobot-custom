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
    assert resp.error_type == "unknown"


async def test_provider_chat_classifies_timeout_error(monkeypatch) -> None:
    async def _boom(**kwargs):
        raise TimeoutError("request timed out")

    monkeypatch.setattr("nanobot.providers.litellm_provider.acompletion", _boom)

    provider = LiteLLMProvider(api_key="k", default_model="openai/gpt-4o-mini")
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert resp.error_type == "timeout"


def test_provider_classify_error_uses_precise_format_phrases() -> None:
    assert (
        LiteLLMProvider._classify_error(None, "invalid json schema for tool arguments")
        == "format"
    )
    assert LiteLLMProvider._classify_error(None, "unsupported format image/webp") == "unknown"


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


def test_provider_apply_cache_control_marks_system_and_last_tool() -> None:
    provider = LiteLLMProvider(api_key="k")
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hi"},
    ]
    tools = [
        {"type": "function", "function": {"name": "one"}},
        {"type": "function", "function": {"name": "two"}},
    ]

    new_messages, new_tools = provider._apply_cache_control(messages, tools)
    assert isinstance(new_messages[0]["content"], list)
    assert new_messages[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert new_tools is not None
    assert new_tools[-1]["cache_control"]["type"] == "ephemeral"
    assert "cache_control" not in tools[-1]


def test_provider_supports_cache_control_for_claude_only() -> None:
    provider = LiteLLMProvider(api_key="k")
    assert provider._supports_cache_control("anthropic/claude-3-5-sonnet")
    assert not provider._supports_cache_control("openai/gpt-4o-mini")


async def test_provider_chat_injects_cache_control_for_supported_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_completion(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    monkeypatch.setattr("nanobot.providers.litellm_provider.acompletion", _fake_completion)
    provider = LiteLLMProvider(api_key="k", default_model="anthropic/claude-3-5-sonnet")
    await provider.chat(
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "ping"}}],
        model="anthropic/claude-3-5-sonnet",
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    messages = kwargs["messages"]
    assert isinstance(messages, list)
    assert messages[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert kwargs["tool_choice"] == "auto"


async def test_provider_chat_passes_required_tool_choice(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_completion(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    monkeypatch.setattr("nanobot.providers.litellm_provider.acompletion", _fake_completion)
    provider = LiteLLMProvider(api_key="k", default_model="openai/gpt-4o-mini")
    await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "ping"}}],
        tool_choice="required",
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["tool_choice"] == "required"


async def test_provider_chat_omits_tool_choice_without_tools(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_completion(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    monkeypatch.setattr("nanobot.providers.litellm_provider.acompletion", _fake_completion)
    provider = LiteLLMProvider(api_key="k", default_model="openai/gpt-4o-mini")
    await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=None, tool_choice="required")

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "tool_choice" not in kwargs
