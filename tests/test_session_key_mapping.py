import json
from pathlib import Path

from nanobot.session.manager import SessionManager


def test_session_path_encoding_avoids_legacy_collision(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    manager = SessionManager(tmp_path)

    key_a = "telegram:chat_1"
    key_b = "telegram_chat:1"

    assert manager._get_legacy_session_path(key_a) == manager._get_legacy_session_path(key_b)
    assert manager._get_session_path(key_a) != manager._get_session_path(key_b)


def test_list_sessions_uses_exact_key_from_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    manager = SessionManager(tmp_path)

    key = "discord:team_alpha_room_1"
    session = manager.get_or_create(key)
    session.add_message("user", "hello")
    manager.save(session)

    entries = manager.list_sessions()
    keys = [item["key"] for item in entries]
    assert key in keys


def test_load_legacy_file_and_save_to_encoded_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    manager = SessionManager(tmp_path)

    key = "telegram:legacyroom"
    legacy_path = manager._get_legacy_session_path(key)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_type": "metadata",
        "created_at": "2026-02-16T00:00:00",
        "updated_at": "2026-02-16T00:00:00",
        "metadata": {"v": 1},
    }
    legacy_path.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n" + json.dumps({"role": "user", "content": "old"}) + "\n",
        encoding="utf-8",
    )

    session = manager.get_or_create(key)
    assert session.get_history() == [{"role": "user", "content": "old"}]
    manager.save(session)

    new_path = manager._get_session_path(key)
    assert new_path.exists()
    metadata = json.loads(new_path.read_text(encoding="utf-8").splitlines()[0])
    assert metadata["key"] == key

