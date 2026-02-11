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
    
    # === 声称执行了命令但实际没调用工具 ===
    (
        "claimed_execution",
        r"(我刚刚|我已经|我刚|刚刚)(执行|运行|调用)了.*?(命令|指令|脚本)",
        0.9  # 非常高 — 没调用工具却声称执行了命令
    ),
    (
        "claimed_command_output",
        r"(执行|运行).*?(du|df|ls|ps|top|free|cat|find|grep|docker|systemctl)\s",
        0.85
    ),
    (
        "fake_path_listing",
        r"(\d+(\.\d+)?[KMGT]?\s+/[\w./]+\n){3,}",  # du/df 格式的路径列表
        0.85
    ),
    
    # === 新增：URL/账号编造检测 ===
    (
        "fabricated_x_url",
        r"https?://(?:x\.com|twitter\.com)/\w+/status/\d{15,}",  # X 链接
        0.85  # 高置信度 - 输出未验证链接很可疑
    ),
    (
        "fabricated_social_account",
        r"@[A-Za-z_][A-Za-z0-9_]{2,14}\s*[-–—:]\s*[^\n]+",  # @账号 - 描述格式
        0.65
    ),
    (
        "overconfident_verification",
        r"(我已|我刚刚|通过.*?)(验证|确认|检查|核实).{0,20}(存在|真实|有效)",
        0.75  # 声称已验证但可能没有
    ),
    (
        "fabricated_demo_link",
        r"(Demo|演示|视频)链接[:：]?\s*https?://",
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
    # 检查模型是否支持工具调用
    from nanobot.config.model_capabilities import supports_function_calling
    if supports_function_calling(model):
        # 模型支持工具但这次没调用 — 提示重试
        return (
            "⚠️ **检测到异常**\n\n"
            "我刚才试图用文字描述操作结果，而不是真正执行命令。"
            "这是不可接受的行为。\n\n"
            "**请重新发送你的请求**，我会正确使用工具来执行。"
        )
    else:
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


def should_block_output(text: str) -> tuple[bool, str]:
    """
    检查输出是否应该被拦截（包含编造的 URL）。
    
    Args:
        text: 模型的响应文本
        
    Returns:
        (should_block, reason) 元组
    """
    try:
        from nanobot.security.url_validator import extract_and_validate_urls
        
        # 检查所有 X 链接
        url_results = extract_and_validate_urls(text)
        for result in url_results:
            if not result.is_valid:
                return True, f"检测到无效 URL: {result.original} ({result.reason})"
        
        return False, ""
    except ImportError:
        # 如果 url_validator 模块不可用，跳过检查
        return False, ""


def filter_fabricated_content(text: str) -> tuple[str, list[str]]:
    """
    过滤文本中编造的内容（无效 URL）。
    
    Args:
        text: 原始响应文本
        
    Returns:
        (filtered_text, warnings) 元组
    """
    warnings = []
    filtered = text
    
    try:
        from nanobot.security.url_validator import extract_and_validate_urls
        
        url_results = extract_and_validate_urls(text)
        for result in url_results:
            if not result.is_valid:
                warnings.append(f"{result.original}: {result.reason}")
                # 用警告替换无效 URL
                filtered = filtered.replace(
                    result.original,
                    f"[⚠️ 链接已移除: {result.reason}]"
                )
    except ImportError:
        pass
    
    return filtered, warnings
