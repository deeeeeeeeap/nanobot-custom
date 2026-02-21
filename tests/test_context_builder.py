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

    assert "读写 MEMORY.md 和结构化记忆" in prompt
    assert "USER.md=用户偏好" not in prompt


def test_system_prompt_skips_mojibake_bootstrap(tmp_path):
    # Use explicit mojibake markers to keep fixture readable.
    mojibake_sample = " ".join(["\ufffd"] * 8)
    (tmp_path / "AGENTS.md").write_text(mojibake_sample, encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "内容因编码异常已跳过" in prompt
    assert "\ufffd" not in prompt


def test_mojibake_detector_does_not_flag_repeated_fang_character():
    assert ContextBuilder._looks_mojibake("\u9983" * 8) is False
