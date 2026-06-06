import httpx

from nanobot.providers.openai_responses.converters import convert_messages_to_payload
from nanobot.providers.openai_responses.parsing import parse_response_output
from nanobot.providers.openai_responses_provider import OpenAIResponsesProvider


async def test_openai_responses_provider_posts_compatible_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        },
                        {
                            "type": "function_call",
                            "id": "fc_abc",
                            "call_id": "call_12345678",
                            "name": "read_file",
                            "arguments": "{\"path\":\"README.md\"}",
                        },
                    ],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 4,
                        "total_tokens": 7,
                    },
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("nanobot.providers.openai_responses_provider.httpx.AsyncClient", FakeClient)

    provider = OpenAIResponsesProvider(
        api_key="sk-test",
        api_base="https://relay.example/v1",
        default_model="gpt-5-mini",
        extra_headers={"X-Relay": "yes"},
        extra_body={"parallel_tool_calls": False},
    )
    result = await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        max_tokens=12,
    )

    assert captured["url"] == "https://relay.example/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["headers"]["X-Relay"] == "yes"
    assert captured["json"]["stream"] is False
    assert captured["json"]["max_output_tokens"] == 12
    assert captured["json"]["parallel_tool_calls"] is False
    assert result.content == "ok"
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].id == "call_12345678|fc_abc"
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "README.md"}
    assert result.usage["total_tokens"] == 7


def test_openai_responses_provider_classifies_localized_errors() -> None:
    assert OpenAIResponsesProvider._error_type(None, "访问量过大") == "rate_limit"
    assert OpenAIResponsesProvider._error_type(None, "账户欠费或余额不足") == "billing"


async def test_openai_responses_provider_rejects_non_object_response(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            return httpx.Response(
                200,
                json=[],
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("nanobot.providers.openai_responses_provider.httpx.AsyncClient", FakeClient)

    provider = OpenAIResponsesProvider(api_key="sk-test", api_base="https://relay.example/v1")
    result = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.finish_reason == "error"
    assert result.error_type == "format"
    assert "non-object response" in result.content


def test_responses_converter_preserves_compound_tool_call_ids() -> None:
    payload, dropped = convert_messages_to_payload(
        messages=[
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1|item_a",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{\"path\":\"a\"}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1|item_a", "content": "ok"},
        ],
        model="gpt-5",
        tools=None,
        tool_choice="auto",
    )

    assert dropped == 0
    assert payload["input"][0]["call_id"] == "call_1"
    assert payload["input"][0]["id"] == "item_a"
    assert payload["input"][1]["call_id"] == "call_1"


def test_responses_parser_deduplicates_output_item_ids_and_extracts_reasoning() -> None:
    response = {
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "why"}],
            },
            {
                "type": "function_call",
                "id": "fc_dup",
                "call_id": "call_a",
                "name": "exec",
                "arguments": "{\"command\":\"pwd\"}",
            },
            {
                "type": "function_call",
                "id": "fc_dup",
                "call_id": "call_b",
                "name": "exec",
                "arguments": "{\"command\":\"ls\"}",
            },
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }

    parsed = parse_response_output(response)
    assert parsed.reasoning_content == "why"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].id == "call_a|fc_dup"
    assert parsed.usage["total_tokens"] == 3
