from nanobot.agent.memory import MemoryStore


def test_memory_store_layered_context_and_compat(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term("legacy memory")

    profile = store.memories_dir / "profile.md"
    profile.write_text("用户画像信息", encoding="utf-8")
    pref_dir = store.memories_dir / "preferences"
    pref_dir.mkdir(parents=True, exist_ok=True)
    (pref_dir / "m1.md").write_text("喜欢中文\n\n详细内容", encoding="utf-8")

    context = store.get_memory_context()
    assert "Legacy Long-term Memory" in context
    assert "用户画像信息" in context
    assert "喜欢中文" in context


def test_get_memory_detail_safe_path(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    target = store.memories_dir / "entities" / "item.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("实体详情", encoding="utf-8")

    rel = target.relative_to(tmp_path).as_posix()
    assert store.get_memory_detail(rel) == "实体详情"
    assert store.get_memory_detail("../../outside.txt") == ""

