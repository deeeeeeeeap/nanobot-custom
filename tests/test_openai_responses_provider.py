import httpx

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
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "README.md"}
    assert result.usage["total_tokens"] == 7


def test_openai_responses_provider_classifies_localized_errors() -> None:
    assert OpenAIResponsesProvider._error_type(None, "访问量过大") == "rate_limit"
    assert OpenAIResponsesProvider._error_type(None, "账户欠费或余额不足") == "billing"
