from nanobot.agent.memory import MemoryStore


def test_memory_context_redacts_known_secrets(tmp_path):
    store = MemoryStore(tmp_path)
    hex_token = "4b6c2a1ba38be4296efb741acca715c7010e3a3f"
    tg_token = "8275668745:AAGaxh9ztcPmbVpEVo2oCbv3BkRzmzJO7no"
    store.write_long_term(
        "\n".join(
            [
                f"AUTH_TOKEN: {hex_token}",
                f"telegram_bot_token: {tg_token}",
                "普通文本保留",
            ]
        )
    )

    context = store.get_memory_context()
    assert hex_token not in context
    assert tg_token not in context
    assert "[REDACTED]" in context
    assert "普通文本保留" in context


def test_read_long_term_redacts_key_value_secrets(tmp_path):
    store = MemoryStore(tmp_path)
    ct0 = "6bd5c1007f6aa1d66d2636c0d3cdc9cb7d82d43ae819f859f34cb684549ac436"
    store.write_long_term(f"CT0={ct0}\napi_key: demo_secret_value")

    text = store.read_long_term()
    assert ct0 not in text
    assert "demo_secret_value" not in text
    assert "CT0= [REDACTED]" in text
    assert "api_key: [REDACTED]" in text


def test_memory_context_deduplicates_l0_first_line(tmp_path):
    store = MemoryStore(tmp_path)
    pref_dir = store.memories_dir / "preferences"
    event_dir = store.memories_dir / "events"
    pref_dir.mkdir(parents=True, exist_ok=True)
    event_dir.mkdir(parents=True, exist_ok=True)
    (pref_dir / "a.md").write_text("Same Summary\n详情 A", encoding="utf-8")
    (event_dir / "b.md").write_text("same summary\n详情 B", encoding="utf-8")

    context = store.get_memory_context()
    assert context.count("Same Summary") == 1


def test_memory_context_keeps_git_commit_hash(tmp_path):
    store = MemoryStore(tmp_path)
    commit_hash = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    store.write_long_term(f"deployed commit {commit_hash}")

    context = store.get_memory_context()
    assert commit_hash in context
