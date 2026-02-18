import pytest

from nanobot.prompts import render_prompt


def test_render_prompt_success() -> None:
    text = render_prompt(
        "memory_extraction",
        {
            "messages": "[USER] hello",
            "session_key": "telegram:1",
            "output_language": "zh-CN",
        },
    )
    assert "会话标识: telegram:1" in text


def test_render_prompt_missing_variable() -> None:
    with pytest.raises(ValueError):
        render_prompt("memory_extraction", {"messages": "x"})


def test_render_prompt_unknown_template() -> None:
    with pytest.raises(ValueError):
        render_prompt("missing_template", {})

