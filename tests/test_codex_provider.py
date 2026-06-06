from pathlib import Path

from nanobot.providers.codex_provider import CodexProvider, _ParsedSSE
from nanobot.providers.base import ToolCallRequest


class _DummyAuth:
    def __init__(self) -> None:
        self.ensure_calls: list[bool] = []

    async def ensure_valid(self, force: bool = False) -> None:
        self.ensure_calls.append(force)

    @staticmethod
    def get_headers() -> dict[str, str]:
        return {"Authorization": "Bearer token"}


async def test_codex_provider_parses_sse_text_and_tool_calls(tmp_path: Path) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="openai/gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
    )

    async def _fake_send(payload, headers):
        return 200, _ParsedSSE(
            content="done",
            tool_calls=[
                ToolCallRequest(id="call_1", name="read_file", arguments={"path": "README.md"}),
            ],
            reasoning_content=None,
        )

    provider._send_request = _fake_send  # type: ignore[method-assign]
    response = await provider.chat(messages=[{"role": "user", "content": "read file"}])

    assert response.finish_reason == "tool_calls"
    assert response.content == "done"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments["path"] == "README.md"
    assert auth.ensure_calls == [False]


async def test_codex_provider_retries_once_on_401(tmp_path: Path) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
    )

    calls = {"count": 0}

    async def _fake_send(payload, headers):
        calls["count"] += 1
        if calls["count"] == 1:
            return 401, "unauthorized"
        return 200, _ParsedSSE(
            content="ok",
            tool_calls=[],
            reasoning_content=None,
        )

    provider._send_request = _fake_send  # type: ignore[method-assign]
    response = await provider.chat(messages=[{"role": "user", "content": "ping"}])

    assert response.finish_reason == "stop"
    assert response.content == "ok"
    assert calls["count"] == 2
    assert auth.ensure_calls == [False, True]


async def test_codex_provider_injects_server_compaction_and_sanitizes_orphans(
    tmp_path: Path,
) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
        server_compaction_enabled=True,
        compact_threshold=12345,
    )

    captured: dict[str, object] = {}

    async def _fake_send(payload, headers):
        captured["payload"] = payload
        return 200, _ParsedSSE(
            content="ok",
            tool_calls=[],
            reasoning_content=None,
        )

    provider._send_request = _fake_send  # type: ignore[method-assign]
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "ok"},
        {"role": "tool", "tool_call_id": "call_orphan", "name": "read_file", "content": "bad"},
    ]
    await provider.chat(messages=messages)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "context_management" in payload
    assert payload["context_management"][0]["compact_threshold"] == 12345
    outputs = [item for item in payload["input"] if item["type"] == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "call_1"


async def test_codex_provider_classifies_orphan_call_error_as_format(tmp_path: Path) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
    )

    async def _fake_send(payload, headers):
        return 400, "No tool call found for function call output with call_id call_1."

    provider._send_request = _fake_send  # type: ignore[method-assign]
    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.finish_reason == "error"
    assert response.error_type == "format"


async def test_codex_provider_returns_format_error_for_empty_sse(tmp_path: Path) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
    )

    async def _fake_send(payload, headers):
        return 200, _ParsedSSE(content=None, tool_calls=[], reasoning_content=None)

    provider._send_request = _fake_send  # type: ignore[method-assign]
    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.finish_reason == "error"
    assert response.error_type == "format"
    assert "no parseable SSE output" in response.content


async def test_codex_provider_redacts_http_error_body(tmp_path: Path) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
    )

    async def _fake_send(payload, headers):
        return 401, "bad Authorization: Bearer sk-super-secret-token api_key=relay-secret"

    provider._send_request = _fake_send  # type: ignore[method-assign]
    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.finish_reason == "error"
    assert response.error_type == "auth_expired"
    assert response.content is not None
    assert "sk-super-secret-token" not in response.content
    assert "relay-secret" not in response.content
    assert "[REDACTED]" in response.content


async def test_codex_provider_redacts_runtime_error(tmp_path: Path) -> None:
    auth = _DummyAuth()
    provider = CodexProvider(
        default_model="gpt-5.3-codex",
        auth=auth,
        responses_url="http://localhost:8081/v1/responses",
    )

    async def _fake_send(payload, headers):
        raise RuntimeError('upstream {"api_key":"sk-super-secret-token"} failed')

    provider._send_request = _fake_send  # type: ignore[method-assign]
    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.finish_reason == "error"
    assert response.content is not None
    assert "sk-super-secret-token" not in response.content
    assert "[REDACTED]" in response.content
