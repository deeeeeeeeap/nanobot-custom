"""Narrow query-time microcompaction for tool-result messages."""

from __future__ import annotations

import json
from typing import Any

COMPACTABLE_TOOL_NAMES = frozenset({"exec", "read_file", "web_search", "web_fetch"})
TOOL_RESULT_CLEARED_MESSAGE = "[Old tool result content cleared]"


def rough_token_count_estimation(content: str, bytes_per_token: int = 4) -> int:
    """按固定字节/token比率估算token数。"""
    return round(len(content) / bytes_per_token)


def _json_stringify(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def estimate_tool_result_tokens(content: Any) -> int:
    """估算单个tool-result payload的token量。"""
    if content is None:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if not isinstance(item, dict):
                total += rough_token_count_estimation(_json_stringify(item))
                continue
            item_type = item.get("type")
            if item_type == "text":
                total += rough_token_count_estimation(str(item.get("text", "")))
            elif item_type in {"image", "document"}:
                total += 2000
            else:
                total += rough_token_count_estimation(_json_stringify(item))
        return total
    return rough_token_count_estimation(_json_stringify(content))


def estimate_tool_result_message_tokens(message: dict[str, Any]) -> int:
    """估算单条tool-result消息的token量。"""
    if message.get("role") != "tool":
        return 0
    return estimate_tool_result_tokens(message.get("content"))


def microcompact_messages(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = 4,
    large_result_token_threshold: int = 800,
) -> list[dict[str, Any]]:
    """返回压缩后的消息副本，不修改原始输入。

    使用浅拷贝+选择性复制，仅对被清理的消息做dict复制，
    避免对整个消息列表做deepcopy的性能开销。
    """
    if keep_recent < 1:
        keep_recent = 1

    compactable_indices: list[int] = []
    for index, message in enumerate(messages):
        if (
            message.get("role") == "tool"
            and message.get("name") in COMPACTABLE_TOOL_NAMES
        ):
            compactable_indices.append(index)

    if not compactable_indices:
        return list(messages)

    # 只对需要清理的消息索引做浅拷贝
    indices_to_clear: set[int] = set()
    keep_ids = set(compactable_indices[-keep_recent:])
    for index in compactable_indices:
        if index in keep_ids:
            continue
        if estimate_tool_result_message_tokens(messages[index]) < large_result_token_threshold:
            continue
        indices_to_clear.add(index)

    if not indices_to_clear:
        return list(messages)

    result = list(messages)  # 浅拷贝列表
    for index in indices_to_clear:
        result[index] = {**result[index], "content": TOOL_RESULT_CLEARED_MESSAGE}
    return result
