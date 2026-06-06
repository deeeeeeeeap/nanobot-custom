"""Codex Responses API conversion helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any


def _extract_text(content: Any) -> str:
    """Extract plain text from Chat Completions style content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content)


def flatten_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert Chat Completions nested function tools to Responses API format."""
    if not tools:
        return []

    converted: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            fn = tool["function"]
            item: dict[str, Any] = {
                "type": "function",
                "name": str(fn.get("name", "")),
            }
            if "description" in fn:
                item["description"] = fn["description"]
            if "parameters" in fn:
                item["parameters"] = fn["parameters"]
            converted.append(item)
            continue
        converted.append(tool)
    return converted


def sanitize_input_items(input_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop orphan function_call_output records without a matching function_call."""
    call_ids = {
        str(item.get("call_id", "")).strip()
        for item in input_items
        if item.get("type") == "function_call" and str(item.get("call_id", "")).strip()
    }
    if not call_ids:
        filtered = [item for item in input_items if item.get("type") != "function_call_output"]
        return filtered, len(input_items) - len(filtered)

    filtered: list[dict[str, Any]] = []
    dropped = 0
    for item in input_items:
        if item.get("type") != "function_call_output":
            filtered.append(item)
            continue
        call_id = str(item.get("call_id", "")).strip()
        if call_id in call_ids:
            filtered.append(item)
        else:
            dropped += 1
    return filtered, dropped


def convert_messages_to_payload(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None,
    tool_choice: str,
    stream: bool = True,
    enable_server_compaction: bool = False,
    compact_threshold: int = 80000,
) -> tuple[dict[str, Any], int]:
    """Convert Chat Completions messages to a Responses API payload."""
    system_sections: list[str] = []
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content")

        if role == "system":
            text = _extract_text(content).strip()
            if text:
                system_sections.append(text)
            continue

        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(msg.get("tool_call_id", "")),
                    "output": _extract_text(content),
                }
            )
            continue

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    args = fn.get("arguments", "{}")
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    elif not isinstance(args, str):
                        args = str(args)
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                            "name": str(fn.get("name", "")),
                            "arguments": args,
                        }
                    )

            text = _extract_text(content).strip()
            if text:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            continue

        input_items.append(
            {
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": _extract_text(content)}],
            }
        )

    input_items, dropped = sanitize_input_items(input_items)
    payload: dict[str, Any] = {
        "model": model,
        "instructions": "\n\n---\n\n".join(system_sections),
        "input": input_items,
        "stream": stream,
        "store": False,
    }

    converted_tools = flatten_tools(tools)
    if converted_tools:
        payload["tools"] = converted_tools
        payload["tool_choice"] = tool_choice

    if enable_server_compaction:
        payload["context_management"] = [
            {"type": "compaction", "compact_threshold": int(compact_threshold)}
        ]

    return payload, dropped


def parse_response_output(
    output_items: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], str]:
    """Extract text/tool calls/reasoning from response.completed payload."""
    text = ""
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()

    for item in output_items:
        item_type = item.get("type")
        if item_type == "message":
            for content_item in item.get("content", []):
                if not isinstance(content_item, dict):
                    continue
                if content_item.get("type") == "output_text" and content_item.get("text"):
                    text = str(content_item["text"])
                if (
                    content_item.get("type") == "reasoning_summary_text"
                    and content_item.get("text")
                ):
                    reasoning_parts.append(str(content_item["text"]))
            continue

        if item_type == "reasoning":
            summary = item.get("summary")
            if isinstance(summary, list):
                for chunk in summary:
                    if isinstance(chunk, dict) and chunk.get("text"):
                        reasoning_parts.append(str(chunk["text"]))
            elif isinstance(summary, str) and summary.strip():
                reasoning_parts.append(summary.strip())
            continue

        if item_type != "function_call":
            continue

        call_id = str(item.get("call_id", "")).strip() or f"call_{uuid.uuid4().hex[:8]}"
        if call_id in seen_call_ids:
            continue
        seen_call_ids.add(call_id)
        arguments = item.get("arguments", "{}")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        elif not isinstance(arguments, str):
            arguments = str(arguments)
        tool_calls.append(
            {
                "id": call_id,
                "name": str(item.get("name", "")),
                "arguments": arguments,
            }
        )

    reasoning = "\n".join(part for part in reasoning_parts if part).strip()
    return text, tool_calls, reasoning
