from nanobot.providers.codex_adapter import convert_messages_to_payload, parse_response_output


def test_convert_messages_to_payload_sanitizes_orphans_and_flattens_tools() -> None:
    messages = [
        {"role": "system", "content": "你是助手。"},
        {"role": "user", "content": "开始"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "ok"},
        {"role": "tool", "tool_call_id": "call_orphan", "name": "read_file", "content": "bad"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "read",
                "parameters": {"type": "object"},
            },
        }
    ]

    payload, dropped = convert_messages_to_payload(
        messages=messages,
        model="gpt-5.3-codex",
        tools=tools,
        tool_choice="auto",
        enable_server_compaction=True,
        compact_threshold=90000,
    )

    assert dropped == 1
    assert payload["model"] == "gpt-5.3-codex"
    assert payload["instructions"] == "你是助手。"
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["name"] == "read_file"
    assert payload["tools"][0]["type"] == "function"
    assert payload["context_management"][0]["compact_threshold"] == 90000

    outputs = [item for item in payload["input"] if item["type"] == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "call_1"


def test_parse_response_output_extracts_text_tool_calls_and_reasoning() -> None:
    output = [
        {
            "type": "message",
            "content": [
                {"type": "reasoning_summary_text", "text": "先检查文件"},
                {"type": "output_text", "text": "执行完成"},
            ],
        },
        {
            "type": "function_call",
            "call_id": "call_9",
            "name": "read_file",
            "arguments": "{\"path\":\"README.md\"}",
        },
    ]

    text, calls, reasoning = parse_response_output(output)
    assert text == "执行完成"
    assert reasoning == "先检查文件"
    assert calls[0]["id"] == "call_9"
    assert calls[0]["name"] == "read_file"
