"""Convert Chat Completions messages/tools to Responses API payloads."""

from __future__ import annotations

import json
from typing import Any


def _extract_text(content: Any) -> str:
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


def _unique_item_id(item_id: str, used: set[str]) -> str:
    candidate = item_id or "item"
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used:
        suffix += 1
    unique = f"{candidate}_{suffix}"
    used.add(unique)
    return unique


def split_tool_call_id(tool_call_id: Any) -> tuple[str, str | None]:
    """Split a compound `call_id|item_id` value."""
    if isinstance(tool_call_id, str) and tool_call_id:
        if "|" in tool_call_id:
            call_id, item_id = tool_call_id.split("|", 1)
            return call_id or "call_0", item_id or None
        return tool_call_id, None
    return "call_0", None


def _convert_user_message(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "input_text"}:
                converted.append({"type": "input_text", "text": str(item.get("text", ""))})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url")
                if isinstance(url, str) and url:
                    converted.append({"type": "input_image", "image_url": url, "detail": "auto"})
        if converted:
            return {"type": "message", "role": "user", "content": converted}
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": ""}]}


def _sanitize_input_items(input_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
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


def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert nested OpenAI function tools to flat Responses tools."""
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        fn = (tool.get("function") or {}) if tool.get("type") == "function" else tool
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": fn.get("description") or "",
                "parameters": fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {},
            }
        )
    return converted


def convert_messages_to_payload(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    stream: bool = False,
) -> tuple[dict[str, Any], int]:
    """Convert Chat Completions messages to a Responses API request body."""
    system_sections: list[str] = []
    input_items: list[dict[str, Any]] = []
    used_item_ids: set[str] = set()

    for idx, msg in enumerate(messages):
        role = str(msg.get("role", "user"))
        content = msg.get("content")

        if role == "system":
            text = _extract_text(content).strip()
            if text:
                system_sections.append(text)
            continue

        if role == "user":
            input_items.append(_convert_user_message(content))
            continue

        if role == "assistant":
            text = _extract_text(content).strip()
            if text:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "id": _unique_item_id(f"msg_{idx}", used_item_ids),
                        "content": [{"type": "output_text", "text": text}],
                        "status": "completed",
                    }
                )
            for pos, tc in enumerate(msg.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                args = fn.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                elif not isinstance(args, str):
                    args = str(args)
                call_id, item_id = split_tool_call_id(tc.get("id"))
                input_items.append(
                    {
                        "type": "function_call",
                        "id": _unique_item_id(item_id or f"fc_{idx}_{pos}", used_item_ids),
                        "call_id": call_id,
                        "name": str(fn.get("name", "")),
                        "arguments": args,
                    }
                )
            continue

        if role == "tool":
            call_id, _ = split_tool_call_id(msg.get("tool_call_id"))
            output = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            input_items.append({"type": "function_call_output", "call_id": call_id, "output": output})

    input_items, dropped = _sanitize_input_items(input_items)
    payload: dict[str, Any] = {
        "model": model,
        "instructions": "\n\n---\n\n".join(system_sections),
        "input": input_items,
        "stream": stream,
        "store": False,
    }
    converted_tools = convert_tools(tools)
    if converted_tools:
        payload["tools"] = converted_tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    return payload, dropped
