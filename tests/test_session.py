from pathlib import Path

from nanobot.session.manager import SessionManager


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
