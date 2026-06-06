"""Parse OpenAI Responses API response objects."""

from __future__ import annotations

import json
from typing import Any

from nanobot.providers.base import LLMResponse, ToolCallRequest

_FINISH_REASON_MAP = {
    "completed": "stop",
    "incomplete": "length",
    "failed": "error",
    "cancelled": "error",
}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        data = dump()
        return data if isinstance(data, dict) else {}
    try:
        data = vars(value)
    except TypeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _extract_usage(response: dict[str, Any]) -> dict[str, int]:
    usage = _as_dict(response.get("usage"))
    if not usage:
        return {}
    prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _finish_reason(status: Any) -> str:
    return _FINISH_REASON_MAP.get(str(status or "completed"), "stop")


def _output_items(response_or_output: Any) -> tuple[list[Any], dict[str, Any]]:
    if isinstance(response_or_output, list):
        return response_or_output, {"output": response_or_output, "status": "completed"}
    response = _as_dict(response_or_output)
    output = response.get("output") or []
    return output if isinstance(output, list) else [], response


def parse_response_output(response_or_output: Any) -> LLMResponse:
    """Parse a Responses API response or output list into an LLMResponse."""
    output, response = _output_items(response_or_output)
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    seen_item_ids: set[str] = set()

    for raw_item in output:
        item = _as_dict(raw_item)
        item_id = str(item.get("id") or "")
        if item_id:
            if item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)

        item_type = item.get("type")
        if item_type == "message":
            for raw_block in item.get("content") or []:
                block = _as_dict(raw_block)
                block_type = block.get("type")
                if block_type == "output_text" and block.get("text"):
                    content_parts.append(str(block["text"]))
                elif block_type == "reasoning_summary_text" and block.get("text"):
                    reasoning_parts.append(str(block["text"]))
            continue

        if item_type == "reasoning":
            for raw_summary in item.get("summary") or []:
                summary = _as_dict(raw_summary)
                if summary.get("type") == "summary_text" and summary.get("text"):
                    reasoning_parts.append(str(summary["text"]))
            summary_text = item.get("summary")
            if isinstance(summary_text, str) and summary_text.strip():
                reasoning_parts.append(summary_text.strip())
            continue

        if item_type != "function_call":
            continue

        call_id = str(item.get("call_id") or item_id or f"call_{len(tool_calls)}")
        response_item_id = item_id or f"fc_{len(tool_calls)}"
        tool_calls.append(
            ToolCallRequest(
                id=f"{call_id}|{response_item_id}",
                name=str(item.get("name") or ""),
                arguments=_parse_arguments(item.get("arguments") or "{}"),
            )
        )

    return LLMResponse(
        content="".join(content_parts) or None,
        tool_calls=tool_calls,
        finish_reason="tool_calls" if tool_calls else _finish_reason(response.get("status")),
        usage=_extract_usage(response),
        reasoning_content="\n".join(reasoning_parts).strip() or None,
    )
