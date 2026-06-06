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
    assert recovered.get_history() == [{"role": "user", "content": "ok"}]

    recovered.add_message("user", "hello")
    manager.save(recovered)

    reloaded_manager = SessionManager(tmp_path)
    reloaded = reloaded_manager.get_or_create(key)
    assert reloaded.get_history() == [
        {"role": "user", "content": "ok"},
        {"role": "user", "content": "hello"},
    ]


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


def test_session_get_history_keeps_thinking_blocks() -> None:
    session = Session(key="telegram:thinking")
    session.add_message("user", "start")
    session.add_message(
        "assistant",
        "answer",
        thinking_blocks=[{"type": "thinking", "text": "step-a"}],
    )

    history = session.get_history()
    assert history[1]["thinking_blocks"] == [{"type": "thinking", "text": "step-a"}]


def test_session_metadata_round_trip_preserves_restore_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    key = "telegram:meta-roundtrip"
    manager = SessionManager(tmp_path)
    session = manager.get_or_create(key)
    session.set_metadata(
        last_assistant_timestamp="2026-03-31T10:11:12",
        compaction_failure_streak=3,
        microcompact_stats={"turns": 8, "tokens_saved": 1234},
        cost_tracker_state={"totalCostUSD": 1.25, "modelUsage": {"claude": {"inputTokens": 10}}},
        mode="coordinator",
        worker_summary="handled file cleanup",
    )
    session.add_message("user", "persist me")
    manager.save(session)

    reloaded = SessionManager(tmp_path).get_or_create(key)
    assert reloaded.metadata == session.metadata
    assert reloaded.updated_at >= reloaded.created_at


def test_session_metadata_loads_legacy_files_without_new_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    key = "telegram:meta-legacy"
    manager = SessionManager(tmp_path)
    path = manager._get_session_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"_type":"metadata","key":"telegram:meta-legacy","created_at":"2026-02-16T00:00:00","updated_at":"2026-02-16T00:00:01","metadata":{"custom":"value"}}\n'
        '{"role":"user","content":"hello"}\n',
        encoding="utf-8",
    )

    session = manager.get_or_create(key)
    assert session.key == key
    assert session.metadata == {"custom": "value"}
    assert session.get_history() == [{"role": "user", "content": "hello"}]
    assert session.updated_at.isoformat().startswith("2026-02-16T00:00:01")


def test_session_last_consolidated_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    manager = SessionManager(tmp_path)
    session = manager.get_or_create("telegram:consolidated")
    session.add_message("user", "old")
    session.add_message("user", "new")
    session.last_consolidated = 1
    manager.save(session)

    reloaded = SessionManager(tmp_path).get_or_create("telegram:consolidated")
    assert reloaded.last_consolidated == 1
    assert reloaded.get_history() == [{"role": "user", "content": "new"}]


def test_session_get_history_token_budget_starts_at_user() -> None:
    session = Session(key="telegram:budget")
    session.add_message("user", "first " + "x" * 200)
    session.add_message("assistant", "old answer " + "y" * 200)
    session.add_message("user", "latest request")
    session.add_message("assistant", "latest answer")

    history = session.get_history(max_messages=10, max_tokens=20)
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "latest request"


def test_session_enforce_file_cap_drops_orphan_tool_prefix() -> None:
    session = Session(key="telegram:cap")
    session.add_message("user", "keep-anchor")
    session.add_message(
        "assistant",
        "",
        tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
    )
    session.add_message("tool", "old result", tool_call_id="call-1", name="read_file")
    session.add_message("user", "new request")
    session.add_message("assistant", "new answer")

    dropped, _ = session.retain_recent_legal_suffix(2)
    assert dropped
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "new request"


def test_session_metadata_ignores_non_dict_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    key = "telegram:meta-invalid"
    manager = SessionManager(tmp_path)
    path = manager._get_session_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"_type":"metadata","created_at":"2026-02-16T00:00:00","metadata":"oops"}\n'
        '{"role":"user","content":"hello"}\n',
        encoding="utf-8",
    )

    session = manager.get_or_create(key)
    assert session.metadata == {}
