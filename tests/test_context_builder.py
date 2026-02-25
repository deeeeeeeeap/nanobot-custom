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


def test_build_messages_moves_runtime_context_to_user_message(tmp_path):
    builder = ContextBuilder(tmp_path)
    messages = builder.build_messages(
        history=[],
        current_message="执行检查",
        channel="telegram",
        chat_id="42",
    )

    system_msg = messages[0]
    user_msg = messages[-1]
    assert system_msg["role"] == "system"
    assert "## Current Session" not in system_msg["content"]
    assert user_msg["role"] == "user"
    assert "[runtime] session=telegram:42" in user_msg["content"]


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
