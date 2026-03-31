from copy import deepcopy

from nanobot.agent.microcompact import (
    TOOL_RESULT_CLEARED_MESSAGE,
    estimate_tool_result_message_tokens,
    estimate_tool_result_tokens,
    microcompact_messages,
)


def test_tool_result_token_estimator_handles_strings_and_blocks() -> None:
    assert estimate_tool_result_tokens("abcd") == 1
    assert (
        estimate_tool_result_tokens(
            [
                {"type": "text", "text": "abcd"},
                {"type": "document", "data": "ignored"},
            ]
        )
        == 2001
    )


def test_microcompact_returns_new_list_and_keeps_recent_results() -> None:
    messages = [
        {"role": "user", "content": "start"},
        {"role": "tool", "name": "exec", "content": "x" * 4000},
        {"role": "tool", "name": "read_file", "content": "y" * 4000},
        {"role": "tool", "name": "web_search", "content": "z" * 4000},
        {"role": "tool", "name": "web_fetch", "content": "w" * 4000},
        {"role": "tool", "name": "exec", "content": "keep me intact"},
    ]
    original = deepcopy(messages)

    compacted = microcompact_messages(messages, keep_recent=2, large_result_token_threshold=800)

    assert messages == original
    assert compacted is not messages
    assert compacted[1]["content"] == TOOL_RESULT_CLEARED_MESSAGE
    assert compacted[2]["content"] == TOOL_RESULT_CLEARED_MESSAGE
    assert compacted[3]["content"] == TOOL_RESULT_CLEARED_MESSAGE
    assert compacted[4]["content"] == "w" * 4000
    assert compacted[5]["content"] == "keep me intact"


def test_microcompact_ignores_non_compactable_tool_names() -> None:
    messages = [
        {"role": "tool", "name": "message", "content": "x" * 4000},
        {"role": "tool", "name": "other", "content": "y" * 4000},
    ]

    compacted = microcompact_messages(messages, keep_recent=1, large_result_token_threshold=1)

    assert compacted == messages


def test_estimator_only_counts_tool_messages() -> None:
    assert estimate_tool_result_message_tokens({"role": "assistant", "content": "x"}) == 0
    assert estimate_tool_result_message_tokens(
        {"role": "tool", "name": "exec", "content": "abcd"}
    ) == 1
