"""URL/账号输出前置验证器

检测模型试图输出的 URL 和社交媒体账号，在发送前强制验证。
防止模型编造不存在的链接和账号。
"""

import re
from typing import NamedTuple


class ValidationResult(NamedTuple):
    """URL 验证结果"""
    is_valid: bool      # True=有效, False=无效, None=无法确定
    reason: str         # 验证原因
    original: str       # 原始 URL
    url_type: str       # 链接类型: x, generic


# X (Twitter) Status ID 有效范围（基于 Snowflake ID 时间戳）
# Twitter Snowflake ID 从 2010 年开始，当前 ID 约在 1880000000000000000 左右
X_STATUS_ID_MIN = 1000000000000000000   # 约 2017 年
X_STATUS_ID_MAX = 1920000000000000000   # 约 2026 年底（预留一些余量）

# 检测 X (Twitter) URL 模式
X_URL_PATTERN = re.compile(
    r'https?://(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/(\d+)',
    re.IGNORECASE
)

# 检测任意 URL
GENERIC_URL_PATTERN = re.compile(
    r'https?://[^\s<>\[\]"\']+',
    re.IGNORECASE
)

# 社交媒体账号模式
SOCIAL_ACCOUNT_PATTERN = re.compile(
    r'@([A-Za-z_][A-Za-z0-9_]{1,14})(?:\s*[-–—:]\s*|\s+)([^\n]{3,50})',
    re.UNICODE
)


def validate_x_url(url: str) -> ValidationResult:
    """
    验证 X (Twitter) URL 的 Status ID 是否在合理范围内。
    
    Twitter/X 使用 Snowflake ID，ID 包含时间戳信息。
    如果 ID 超出当前时间范围，说明是编造的。
    
    Args:
        url: X 链接
        
    Returns:
        ValidationResult 包含验证结果
    """
    match = X_URL_PATTERN.search(url)
    if not match:
        return ValidationResult(True, "非 X 链接，跳过验证", url, "generic")
    
    username, status_id_str = match.groups()
    
    try:
        status_id = int(status_id_str)
    except ValueError:
        return ValidationResult(False, "Status ID 格式无效", url, "x")
    
    # ID 范围检查
    if status_id < X_STATUS_ID_MIN:
        return ValidationResult(
            False, 
            f"Status ID 过小（{status_id}），早于有效时间范围",
            url,
            "x"
        )
    
    if status_id > X_STATUS_ID_MAX:
        return ValidationResult(
            False, 
            f"Status ID 过大（{status_id}），超出当前时间范围（可能是编造的）",
            url,
            "x"
        )
    
    # ID 长度检查（Twitter ID 通常是 18-19 位）
    if len(status_id_str) < 17 or len(status_id_str) > 20:
        return ValidationResult(
            False,
            f"Status ID 长度异常（{len(status_id_str)} 位）",
            url,
            "x"
        )
    
    return ValidationResult(True, "Status ID 在有效范围内", url, "x")


def extract_and_validate_urls(text: str) -> list[ValidationResult]:
    """
    从文本中提取所有 X 链接并验证。
    
    Args:
        text: 要检查的文本
        
    Returns:
        ValidationResult 列表
    """
    results = []
    
    # 查找所有 X 链接
    for match in X_URL_PATTERN.finditer(text):
        url = match.group(0)
        results.append(validate_x_url(url))
    
    return results


def extract_social_accounts(text: str) -> list[tuple[str, str]]:
    """
    从文本中提取社交媒体账号引用。
    
    格式: @username - 描述
    
    Args:
        text: 要检查的文本
        
    Returns:
        [(username, description), ...] 列表
    """
    accounts = []
    for match in SOCIAL_ACCOUNT_PATTERN.finditer(text):
        username, description = match.groups()
        accounts.append((username, description.strip()))
    return accounts


def filter_invalid_urls(text: str) -> tuple[str, list[str]]:
    """
    过滤文本中的无效 URL，用警告替换。
    
    Args:
        text: 原始文本
        
    Returns:
        (filtered_text, removed_urls) 元组
    """
    results = extract_and_validate_urls(text)
    removed = []
    filtered_text = text
    
    for result in results:
        if not result.is_valid:
            removed.append(result.original)
            # 用警告替换无效 URL
            warning = f"[⚠️ 链接已移除: {result.reason}]"
            filtered_text = filtered_text.replace(result.original, warning)
    
    return filtered_text, removed


def should_warn_about_urls(text: str) -> tuple[bool, str]:
    """
    检查文本是否包含需要警告的 URL 问题。
    
    Args:
        text: 要检查的文本
        
    Returns:
        (should_warn, warning_message) 元组
    """
    results = extract_and_validate_urls(text)
    invalid = [r for r in results if not r.is_valid]
    
    if not invalid:
        return False, ""
    
    warnings = []
    for r in invalid:
        warnings.append(f"- {r.original}: {r.reason}")
    
    message = (
        "⚠️ **检测到可疑链接**\n\n"
        "以下链接可能是编造的：\n"
        + "\n".join(warnings)
        + "\n\n请使用搜索工具获取真实链接。"
    )
    
    return True, message
