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
