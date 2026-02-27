import json
import runpy
import sys
import types
from pathlib import Path


def _load_bridge_module(tmp_path: Path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "token", "account_id": "acct-1"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    flask_stub = types.ModuleType("flask")

    class _DummyApp:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def _decorator(func):
                return func

            return _decorator

    flask_stub.Flask = _DummyApp
    flask_stub.request = types.SimpleNamespace(get_json=lambda force=False: {})
    flask_stub.jsonify = lambda payload: payload
    monkeypatch.setitem(sys.modules, "flask", flask_stub)

    requests_stub = types.ModuleType("requests")
    requests_stub.exceptions = types.SimpleNamespace(RequestException=Exception)
    requests_stub.post = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("requests.post should not be called in unit tests")
    )
    monkeypatch.setitem(sys.modules, "requests", requests_stub)

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "codex_bridge.py"
    return runpy.run_path(str(script_path))


def test_convert_to_responses_api_uses_strict_allowlist(monkeypatch, tmp_path: Path) -> None:
    module = _load_bridge_module(tmp_path, monkeypatch)
    convert = module["convert_to_responses_api"]

    payload = {
        "model": "openai/gpt-5.3-codex",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 256,
        "temperature": 0.1,
        "foo": "bar",
    }
    result = convert(payload)
    assert set(result.keys()) == {"model", "instructions", "input", "stream", "store"}


def test_convert_to_responses_api_includes_tools_conditionally(monkeypatch, tmp_path: Path) -> None:
    module = _load_bridge_module(tmp_path, monkeypatch)
    convert = module["convert_to_responses_api"]

    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "tool_choice": "auto",
    }
    result = convert(payload)
    assert "tools" in result
    assert "tool_choice" in result


def test_convert_to_responses_api_drops_temperature_and_max_output_tokens(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_bridge_module(tmp_path, monkeypatch)
    convert = module["convert_to_responses_api"]

    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1000,
        "temperature": 0.2,
    }
    result = convert(payload)
    assert "temperature" not in result
    assert "max_output_tokens" not in result


def test_sanitize_input_items_drops_orphans(monkeypatch, tmp_path: Path) -> None:
    module = _load_bridge_module(tmp_path, monkeypatch)
    sanitize_input_items = module["sanitize_input_items"]

    filtered, dropped = sanitize_input_items(
        [
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"type": "function_call_output", "call_id": "call_orphan", "output": "bad"},
        ]
    )
    assert dropped == 1
    assert len([item for item in filtered if item.get("type") == "function_call_output"]) == 1


def test_sanitize_chat_messages_drops_orphan_tool(monkeypatch, tmp_path: Path) -> None:
    module = _load_bridge_module(tmp_path, monkeypatch)
    sanitize_chat_messages = module["sanitize_chat_messages"]

    filtered, dropped = sanitize_chat_messages(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "x"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            {"role": "tool", "tool_call_id": "call_orphan", "content": "bad"},
        ]
    )
    assert dropped == 1
    assert [msg.get("tool_call_id") for msg in filtered if msg.get("role") == "tool"] == ["call_1"]
