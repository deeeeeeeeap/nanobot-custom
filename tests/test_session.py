import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nanobot.session.manager import Session, SessionManager


def test_session_manager_recovers_from_corrupted_jsonl(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    key = "telegram:chat42"
    manager = SessionManager(tmp_path)
    session_path = manager._get_session_path(key)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        '{"_type":"metadata","created_at":"2026-02-16T00:00:00","metadata":{}}\n'
        '{"role":"user","content":"ok"}\n'
        "{bad-json}\n",
        encoding="utf-8",
    )

    recovered = manager.get_or_create(key)
    assert recovered.key == key
    assert recovered.messages == []

    recovered.add_message("user", "hello")
    manager.save(recovered)

    reloaded_manager = SessionManager(tmp_path)
    reloaded = reloaded_manager.get_or_create(key)
    assert reloaded.get_history() == [{"role": "user", "content": "hello"}]


def test_session_save_replace_failure_keeps_previous_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    key = "telegram:chat99"
    manager = SessionManager(tmp_path)
    session = manager.get_or_create(key)
    session.add_message("user", "v1")
    manager.save(session)

    session_path = manager._get_session_path(key)
    previous = session_path.read_text(encoding="utf-8")

    def _boom(src: str, dst: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("nanobot.session.manager.os.replace", _boom)
    session.add_message("assistant", "v2")

    with pytest.raises(OSError):
        manager.save(session)

    assert session_path.read_text(encoding="utf-8") == previous
    assert not session_path.with_suffix(session_path.suffix + ".tmp").exists()


def test_session_save_concurrent_writes_keep_jsonl_valid(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    key = "telegram:chat-concurrent"
    manager = SessionManager(tmp_path)

    def _writer(idx: int) -> None:
        s = Session(key=key)
        s.add_message("user", f"message-{idx}")
        manager.save(s)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_writer, range(40)))

    path = manager._get_session_path(key)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]

    assert parsed[0].get("_type") == "metadata"
    assert len(parsed) == 2
    assert parsed[1]["role"] == "user"


def test_session_get_history_keeps_tool_and_reasoning_fields() -> None:
    session = Session(key="telegram:demo")
    session.add_message("user", "请读取文件")
    session.add_message(
        "assistant",
        "",
        tool_calls=[
            {
                "id": "tc-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"},
            }
        ],
        reasoning_content="先读取文件。",
    )
    session.add_message(
        "tool",
        "ok",
        tool_call_id="tc-1",
        name="read_file",
    )

    history = session.get_history()
    assert history[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert history[1]["reasoning_content"] == "先读取文件。"
    assert history[2]["tool_call_id"] == "tc-1"
    assert history[2]["name"] == "read_file"
