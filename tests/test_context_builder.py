from nanobot.agent.context import ContextBuilder


def test_system_prompt_identity_has_no_mojibake(tmp_path):
    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "Carbon-Core" in prompt
    for bad in ("\ufffd", "\ue0ff"):
        assert bad not in prompt


def test_system_prompt_mentions_knowledge_search(tmp_path):
    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "Built-in Knowledge Search" in prompt
    assert "`knowledge_search`" in prompt
    assert "call `knowledge_search` before answering" in prompt


def test_system_prompt_memory_capability_text_updated(tmp_path):
    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "MEMORY.md" in prompt
    assert "USER.md=鐢ㄦ埛鍋忓ソ" not in prompt


def test_system_prompt_skips_mojibake_bootstrap(tmp_path):
    mojibake_sample = " ".join(["\ufffd"] * 8)
    (tmp_path / "AGENTS.md").write_text(mojibake_sample, encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "## AGENTS.md" in prompt
    assert "\ufffd" not in prompt


def test_mojibake_detector_does_not_flag_repeated_fang_character():
    assert ContextBuilder._looks_mojibake("\u9983" * 8) is False


def test_build_messages_separates_runtime_and_user_message(tmp_path):
    builder = ContextBuilder(tmp_path)
    messages = builder.build_messages(
        history=[{"role": "assistant", "content": "历史回复"}],
        current_message="执行检查",
        channel="telegram",
        chat_id="42",
    )

    system_msg = messages[0]
    history_msg = messages[1]
    runtime_msg = messages[2]
    user_msg = messages[3]
    assert system_msg["role"] == "system"
    assert "## Current Session" not in system_msg["content"]
    assert history_msg == {"role": "assistant", "content": "历史回复"}
    assert runtime_msg["role"] == "user"
    assert f"{ContextBuilder._RUNTIME_CONTEXT_TAG} current_time=" in runtime_msg["content"]
    assert f"{ContextBuilder._RUNTIME_CONTEXT_TAG} session=telegram:42" in runtime_msg["content"]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "执行检查"


def test_system_prompt_does_not_list_tool_matrix(tmp_path):
    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()
    assert "## 馃洜锔?鑳藉姏鐭╅樀" not in prompt


def test_system_prompt_replaces_overly_strict_action_rules(tmp_path):
    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "能用三行解决的事，绝不废话五行" not in prompt
    assert "第一轮回复必须包含工具调用" not in prompt
    assert "简单问题直接回答；复杂问题先分析后给结论" in prompt
    assert "收到明确执行请求时优先行动" in prompt


def test_add_assistant_message_preserves_thinking_blocks(tmp_path):
    builder = ContextBuilder(tmp_path)
    messages: list[dict[str, object]] = []

    updated = builder.add_assistant_message(
        messages=messages,
        content="done",
        reasoning_content="brief reasoning",
        thinking_blocks=[{"type": "thinking", "text": "step-1"}],
    )

    assert updated[-1]["role"] == "assistant"
    assert updated[-1]["thinking_blocks"] == [{"type": "thinking", "text": "step-1"}]
    assert updated[-1]["reasoning_content"] == "brief reasoning"


def test_profile_memory_stays_in_system_prompt(tmp_path):
    memory_dir = tmp_path / "memory" / "memories"
    memory_dir.mkdir(parents=True)
    (memory_dir / "profile.md").write_text("用户偏好直接行动。", encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "# Profile" in prompt
    assert "用户偏好直接行动" in prompt


def test_volatile_memory_moves_to_reminder_message(tmp_path):
    memory_root = tmp_path / "memory"
    memories_dir = memory_root / "memories" / "preferences"
    memories_dir.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text("长期约定。", encoding="utf-8")
    (memories_dir / "pref.md").write_text("喜欢简洁回复\n\n细节", encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    system_prompt = builder.build_system_prompt()
    messages = builder.build_messages(
        history=[],
        current_message="继续",
        channel="telegram",
        chat_id="42",
    )

    assert "## Legacy Long-term Memory" not in system_prompt
    assert "## Memory Index (L1)" not in system_prompt
    reminder = messages[1]
    assert reminder["role"] == "user"
    assert ContextBuilder._SYSTEM_REMINDER_TAG in reminder["content"]
    assert "长期约定" in reminder["content"]
    assert "喜欢简洁回复" in reminder["content"]


def test_skills_listing_is_deterministic(tmp_path):
    skills_root = tmp_path / "skills"
    for name in ("z_skill", "a_skill", "m_skill"):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\ndescription: {name}\n---\n{name}",
            encoding="utf-8",
        )

    builder = ContextBuilder(tmp_path)
    first = [item["name"] for item in builder.skills.list_skills(filter_unavailable=False) if item["source"] == "workspace"]
    second = [item["name"] for item in builder.skills.list_skills(filter_unavailable=False) if item["source"] == "workspace"]

    assert first == ["a_skill", "m_skill", "z_skill"]
    assert second == first
