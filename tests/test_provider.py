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


def test_provider_classifies_gateway_localized_errors() -> None:
    assert LiteLLMProvider._classify_error(None, "访问量过大，请稍后再试") == "rate_limit"
    assert LiteLLMProvider._classify_error(None, "账户欠费或余额不足") == "billing"


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
    assert parsed.cache_read_tokens == 0
    assert parsed.cache_creation_tokens == 0


def test_provider_parse_response_extracts_cache_metrics() -> None:
    provider = LiteLLMProvider(api_key="k")
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=20,
            total_tokens=1020,
            cache_read_input_tokens=700,
            cache_creation_input_tokens=200,
        ),
    )

    parsed = provider._parse_response(fake_response, requested_model="anthropic/claude-3-5-sonnet")
    assert parsed.cache_read_tokens == 700
    assert parsed.cache_creation_tokens == 200


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


async def test_provider_chat_passes_reasoning_effort(monkeypatch) -> None:
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
        reasoning_effort="high",
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["reasoning_effort"] == "high"


def test_provider_sanitize_messages_whitelist_and_assistant_content() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "x"}],
            "extra": "drop",
        },
        {
            "role": "tool",
            "tool_call_id": "abc",
            "name": "read_file",
            "content": "ok",
            "metadata": {"x": 1},
        },
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)
    assert sanitized[0]["role"] == "assistant"
    assert sanitized[0]["content"] is None
    assert "extra" not in sanitized[0]
    assert sanitized[1]["name"] == "read_file"
    assert "metadata" not in sanitized[1]


def test_provider_sanitize_messages_keeps_thinking_blocks() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "ok",
            "thinking_blocks": [{"type": "thinking", "text": "step"}],
            "extra": "drop",
        }
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)
    assert sanitized[0]["thinking_blocks"] == [{"type": "thinking", "text": "step"}]
    assert "extra" not in sanitized[0]


def test_provider_normalize_short_tool_call_ids_keeps_references_in_sync() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "a1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "a1", "name": "read_file", "content": "ok"},
    ]

    normalized = LiteLLMProvider._normalize_short_tool_call_ids(messages)
    assistant_call_id = normalized[0]["tool_calls"][0]["id"]
    tool_call_id = normalized[1]["tool_call_id"]
    assert assistant_call_id == tool_call_id
    assert len(assistant_call_id) >= 8


def test_provider_sanitize_empty_content_blocks() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": ""},
                {"type": "text", "text": "   "},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }
    ]
    sanitized = LiteLLMProvider._sanitize_empty_content(messages)
    assert isinstance(sanitized[0]["content"], list)
    assert len(sanitized[0]["content"]) == 1
    assert sanitized[0]["content"][0]["type"] == "image_url"


def test_provider_parse_response_normalizes_empty_reasoning_content() -> None:
    provider = LiteLLMProvider(api_key="k")
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", reasoning_content="", tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )

    parsed = provider._parse_response(fake_response)
    assert parsed.reasoning_content is None


def test_provider_parse_response_extracts_thinking_blocks_and_tool_id_fallback() -> None:
    provider = LiteLLMProvider(api_key="k")
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    reasoning_content=None,
                    thinking_blocks=[{"type": "thinking", "text": "step"}],
                    tool_calls=[
                        SimpleNamespace(
                            id="x1",
                            function=SimpleNamespace(name="ping", arguments="{\"x\": 1}"),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    )

    parsed = provider._parse_response(fake_response)
    assert parsed.thinking_blocks == [{"type": "thinking", "text": "step"}]
    assert len(parsed.tool_calls) == 1
    assert len(parsed.tool_calls[0].id) >= 8


async def test_provider_chat_clamps_max_tokens(monkeypatch) -> None:
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
    await provider.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=0)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["max_tokens"] == 1


async def test_provider_chat_passes_extra_body(monkeypatch) -> None:
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
    provider = LiteLLMProvider(
        api_key="k",
        default_model="openai/gpt-4o-mini",
        extra_body={"metadata": {"route": "vps"}},
    )
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["extra_body"] == {"metadata": {"route": "vps"}}
