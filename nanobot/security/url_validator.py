"""模型输出中的 URL 与账号引用校验工具。"""

from __future__ import annotations

import re
import time
from typing import NamedTuple


class ValidationResult(NamedTuple):
    """URL 校验结果。"""

    is_valid: bool
    reason: str
    original: str
    url_type: str


# X/Twitter Snowflake 常量。
TWITTER_EPOCH_MS = 1288834974657
X_STATUS_ID_MIN = 1000000000000000000


def get_max_valid_status_id() -> int:
    """基于当前时间计算 X 状态 ID 的合理上界。"""
    current_ms = int(time.time() * 1000)
    buffer_ms = 180 * 24 * 60 * 60 * 1000  # 预留 6 个月
    relative_ms = (current_ms + buffer_ms) - TWITTER_EPOCH_MS
    return relative_ms << 22


X_URL_PATTERN = re.compile(
    r"https?://(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/(\d+)",
    re.IGNORECASE,
)

GENERIC_URL_PATTERN = re.compile(
    r"https?://[^\s<>\[\]\"']+",
    re.IGNORECASE,
)

SOCIAL_ACCOUNT_PATTERN = re.compile(
    r"@([A-Za-z_][A-Za-z0-9_]{1,14})(?:\s*[-—–:：]\s*|\s+)([^\n]{3,50})",
    re.UNICODE,
)


def validate_x_url(url: str) -> ValidationResult:
    """按 Snowflake 范围校验 X/Twitter 状态链接。"""
    match = X_URL_PATTERN.search(url)
    if not match:
        return ValidationResult(True, "非 X 链接，跳过校验", url, "generic")

    _, status_id_str = match.groups()
    try:
        status_id = int(status_id_str)
    except ValueError:
        return ValidationResult(False, "状态 ID 格式无效", url, "x")

    if status_id < X_STATUS_ID_MIN:
        return ValidationResult(
            False,
            f"状态 ID 过小（{status_id}），超出有效时间范围",
            url,
            "x",
        )

    if status_id > get_max_valid_status_id():
        return ValidationResult(
            False,
            f"状态 ID 过大（{status_id}），可能为伪造",
            url,
            "x",
        )

    if len(status_id_str) < 17 or len(status_id_str) > 20:
        return ValidationResult(
            False,
            f"状态 ID 长度异常（{len(status_id_str)}）",
            url,
            "x",
        )

    return ValidationResult(True, "状态 ID 在合理范围内", url, "x")


def extract_and_validate_urls(text: str) -> list[ValidationResult]:
    """提取文本中的 X 链接并逐条校验。"""
    results = []
    for match in X_URL_PATTERN.finditer(text):
        url = match.group(0)
        results.append(validate_x_url(url))
    return results


def extract_social_accounts(text: str) -> list[tuple[str, str]]:
    """提取社交账号引用（@username - 描述）。"""
    accounts = []
    for match in SOCIAL_ACCOUNT_PATTERN.finditer(text):
        username, description = match.groups()
        accounts.append((username, description.strip()))
    return accounts


def filter_invalid_urls(text: str) -> tuple[str, list[str]]:
    """将无效链接替换为提示文本。"""
    results = extract_and_validate_urls(text)
    removed: list[str] = []
    filtered_text = text

    for result in results:
        if not result.is_valid:
            removed.append(result.original)
            warning = f"[链接已移除: {result.reason}]"
            filtered_text = filtered_text.replace(result.original, warning)

    return filtered_text, removed


def should_warn_about_urls(text: str) -> tuple[bool, str]:
    """如果检测到可疑链接，生成警告文案。"""
    results = extract_and_validate_urls(text)
    invalid = [r for r in results if not r.is_valid]
    if not invalid:
        return False, ""

    warnings = [f"- {r.original}: {r.reason}" for r in invalid]
    message = (
        "检测到可疑链接。\n\n"
        "以下链接可能为伪造：\n"
        + "\n".join(warnings)
        + "\n\n请使用搜索工具获取可验证的真实链接。"
    )
    return True, message
