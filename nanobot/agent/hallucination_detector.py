"""幻觉检测器 - 识别模型假装执行工具的行为

当模型不支持 Function Calling 时，它可能会"假装"执行命令或搜索，
生成看起来像真实输出的虚假内容。这个模块用于检测和拦截这种行为。
"""

import re
from typing import NamedTuple

from loguru import logger


class HallucinationResult(NamedTuple):
    """幻觉检测结果"""
    is_hallucination: bool   # 是否检测到幻觉
    pattern_name: str        # 匹配的模式名称
    confidence: float        # 置信度 (0.0 - 1.0)


# 幻觉检测模式
# 格式: (模式名称, 正则表达式, 置信度)
HALLUCINATION_PATTERNS: list[tuple[str, str, float]] = [
    # === 假装执行 shell 命令 ===
    (
        "fake_shell_output",
        r"```(bash|sh|shell|console|terminal)\n[\s\S]*?\n```",
        0.7
    ),
    (
        "fake_command_result",
        r"(运行|执行|输出|结果|Output)[:：]\s*\n?\s*```",
        0.8
    ),
    (
        "fake_shell_prompt",
        r"(^|\n)\s*[\$#]\s+(ls|cd|cat|echo|grep|find|ps|df|free|top|htop|docker|systemctl|journalctl)",
        0.6
    ),
    
    # === 假装搜索结果 ===
    (
        "fake_search_results",
        r"(搜索结果|Search Results?)[:：]?\s*\n\s*\d+\.",
        0.9
    ),
    (
        "fake_search_claim",
        r"根据(我的)?(搜索|查询|检索|网络搜索).*?(找到|发现|显示)",
        0.8
    ),
    (
        "fake_web_results",
        r"以下是.*?(搜索|查询).*?结果",
        0.7
    ),
    (
        "fake_url_list",
        r"\d+\.\s+\[.*?\]\(https?://.*?\)",  # Markdown 链接列表格式
        0.6
    ),
    
    # === 假装读取文件 ===
    (
        "fake_file_content",
        r"(文件内容|File content)[:：]?\s*\n?\s*```",
        0.8
    ),
    (
        "fake_file_read",
        r"读取.*?文件.*?[:：]",
        0.7
    ),
    
    # === 假装系统状态 ===
    (
        "fake_system_status",
        r"(系统状态|System Status)[:：]?\s*\n.*?(CPU|内存|Memory|磁盘|Disk)",
        0.8
    ),
    (
        "fake_table_status",
        r"\|\s*项目\s*\|\s*状态\s*\|",  # 假的状态表格
        0.7
    ),
]


def detect_hallucination(
    text: str, 
    tools_were_called: bool,
    model_supports_tools: bool = True
) -> HallucinationResult:
    """
    检测幻觉行为。
    
    Args:
        text: 模型的响应文本
        tools_were_called: 在这次响应过程中是否真正调用了工具
        model_supports_tools: 模型是否支持工具调用
    
    Returns:
        HallucinationResult 包含检测结果
    """
    # 如果真正调用了工具，不是幻觉
    if tools_were_called:
        return HallucinationResult(False, "", 0.0)
    
    # 如果模型支持工具但没有调用，且包含可疑内容，可能是幻觉
    # 如果模型不支持工具，更可能是幻觉
    confidence_multiplier = 1.2 if not model_supports_tools else 1.0
    
    highest_confidence = 0.0
    matched_pattern = ""
    
    for pattern_name, pattern, base_confidence in HALLUCINATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            adjusted_confidence = min(base_confidence * confidence_multiplier, 1.0)
            if adjusted_confidence > highest_confidence:
                highest_confidence = adjusted_confidence
                matched_pattern = pattern_name
    
    # 阈值判断
    is_hallucination = highest_confidence >= 0.7
    
    if is_hallucination:
        logger.warning(
            f"幻觉检测: pattern={matched_pattern}, confidence={highest_confidence:.2f}"
        )
    
    return HallucinationResult(is_hallucination, matched_pattern, highest_confidence)


def create_honest_response(model: str, original_intent: str = "") -> str:
    """
    创建诚实的替代回复。
    
    Args:
        model: 当前使用的模型名称
        original_intent: 用户原本想做什么（可选）
    
    Returns:
        诚实的回复文本
    """
    return (
        f"⚠️ **无法执行此操作**\n\n"
        f"当前模型 `{model}` 不支持工具调用，我无法：\n"
        f"- 🔍 搜索网络\n"
        f"- 💻 执行命令\n"
        f"- 📁 读写文件\n\n"
        f"**解决方案**：切换到支持工具的模型：\n"
        f"`/model gemini-2.5-flash-preview`\n\n"
        f"或者，我可以用我现有的知识来帮助你（但可能不是最新信息）。"
    )


def create_no_tools_available_response() -> str:
    """当工具被禁用时的标准回复"""
    return (
        "我目前处于**纯对话模式**，无法执行系统操作。\n\n"
        "如需使用工具功能，请切换模型：\n"
        "`/model gemini-2.5-flash-preview`"
    )
