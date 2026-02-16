from nanobot.agent.context import ContextBuilder


def test_system_prompt_identity_has_no_mojibake(tmp_path):
    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt()

    assert "Carbon-Core" in prompt
    for bad in ("鈥", "鉁", "\ufffd", "\ue0ff"):
        assert bad not in prompt
