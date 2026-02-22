"""Hallucination guardrails for tool-usage honesty."""

from __future__ import annotations

import re
from typing import NamedTuple

from loguru import logger


class HallucinationResult(NamedTuple):
    """Detection outcome."""

    is_hallucination: bool
    pattern_name: str
    confidence: float


# Format: (pattern_name, regex, confidence)
HALLUCINATION_PATTERNS: list[tuple[str, str, float]] = [
    # Claimed command execution with synthetic shell evidence.
    ("fake_shell_output", r"```(bash|sh|shell|console|terminal)\\n[\\s\\S]*?\\n```", 0.7),
    ("fake_command_result", r"(执行|运行|输出|结果|output)[:：]?\\s*\\n?\\s*```", 0.8),
    ("fake_shell_prompt", r"(^|\\n)\\s*[\\$#]\\s+(ls|cd|cat|echo|grep|find|ps|df|free|top|htop|docker|systemctl|journalctl)", 0.6),
    # Claimed search evidence.
    ("fake_search_results", r"(搜索结果|search results?)[:：]?\\s*\\n\\s*\\d+\\.", 0.9),
    ("fake_search_claim", r"根据(我的)?(搜索|查询|检索|web_search|网络搜索).{0,80}(找到|发现|显示|结果)", 0.8),
    # Claimed file-read evidence.
    ("fake_file_content", r"(文件内容|file content)[:：]?\\s*\\n?\\s*```", 0.8),
    ("fake_file_read", r"读取.*?文件.*?[:：]", 0.7),
    # Claimed system status evidence.
    ("fake_system_status", r"(系统状态|system status)[:：]?\\s*\\n.*?(cpu|memory|disk|负载)", 0.8),
    ("fake_table_status", r"\\|\\s*项目\\s*\\|\\s*状态\\s*\\|", 0.7),
    # Claimed execution without tools.
    (
        "claimed_execution",
        r"(我刚刚|我刚才|我已|我已经).{0,20}(执行|运行|调用).{0,30}(命令|指令|脚本)|"
        r"\b(i just|i already|i have)\b.{0,30}\b(executed|ran|called)\b.{0,30}\b(command|script|tool)\b",
        0.9,
    ),
    ("claimed_command_output", r"(执行|运行).{0,20}(du|df|ls|ps|top|free|cat|find|grep|docker|systemctl)\\b", 0.85),
    ("fake_path_listing", r"(\\d+(\\.\\d+)?[KMGT]?\\s+/[\\w./-]+\\n){3,}", 0.85),
    # URL / account fabrication indicators.
    ("fabricated_x_url", r"https?://(?:x\\.com|twitter\\.com)/\\w+/status/\\d{15,}", 0.85),
    ("fabricated_social_account", r"@[A-Za-z_][A-Za-z0-9_]{2,14}\\s*[-—]\\s*[^\\n]+", 0.65),
    ("overconfident_verification", r"(我已|我刚刚|通过.*?)(验证|确认|检查|核实).{0,20}(存在|真实|有效)", 0.75),
    ("fabricated_demo_link", r"(Demo|演示|视频)链接[:：]?\\s*https?://", 0.7),
]


def _looks_like_execution_plan(text: str) -> bool:
    """Detect plan/progress wording that should not be blocked as fake execution."""
    if not text:
        return False

    lower = text.lower()
    has_plan_intent = (
        any(
            k in text
            for k in ("下一步", "接下来", "我会", "我将", "开始执行", "继续执行", "进度已确认", "如果你同意")
        )
        or any(
            k in lower
            for k in (
                "next step",
                "i will",
                "i'll",
                "going to",
                "proceed",
                "if you agree",
                "started execution",
            )
        )
    )
    has_numbered_steps = bool(re.search(r"(^|\\n)\\s*\\d+[).、]\\s+", text))

    has_completion_claim = (
        any(k in text for k in ("已执行", "执行完成", "已经完成", "执行结果", "结果如下", "命令输出", "exit code"))
        or any(k in lower for k in ("already executed", "execution result", "output:", "command output"))
    )
    has_hard_evidence = (
        "```" in text
        or bool(re.search(r"(^|\\n)\\s*[\\$#]\\s+", text))
        or bool(re.search(r"\\b(traceback|stderr|stdout)\\b", lower))
    )

    return (has_plan_intent or has_numbered_steps) and not (has_completion_claim or has_hard_evidence)


def detect_hallucination(
    text: str,
    tools_were_called: bool,
    model_supports_tools: bool = True,
) -> HallucinationResult:
    """Detect fabricated action/output claims when no tools were actually called."""
    if tools_were_called:
        return HallucinationResult(False, "", 0.0)

    # Planning/progress statements are allowed even without tool calls.
    if _looks_like_execution_plan(text):
        return HallucinationResult(False, "", 0.0)

    confidence_multiplier = 1.2 if not model_supports_tools else 1.0

    highest_confidence = 0.0
    matched_pattern = ""

    for pattern_name, pattern, base_confidence in HALLUCINATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            adjusted_confidence = min(base_confidence * confidence_multiplier, 1.0)
            if adjusted_confidence > highest_confidence:
                highest_confidence = adjusted_confidence
                matched_pattern = pattern_name

    is_hallucination = highest_confidence >= 0.7
    if is_hallucination:
        logger.warning(
            f"Hallucination detected: pattern={matched_pattern}, confidence={highest_confidence:.2f}"
        )

    return HallucinationResult(is_hallucination, matched_pattern, highest_confidence)


def create_honest_response(model: str, original_intent: str = "") -> str:
    """Return a safe user-facing message after a hallucination block."""
    from nanobot.config.model_capabilities import supports_function_calling

    if supports_function_calling(model):
        return (
            "⚠️ **检测到异常**\\n\\n"
            "我刚才试图用文字描述操作结果，而不是真正执行命令。"
            "这是不可接受的行为。\\n\\n"
            "**请重新发送你的请求**，我会正确使用工具来执行。"
        )

    return (
        f"⚠️ **无法执行此操作**\\n\\n"
        f"当前模型 `{model}` 不支持工具调用，我无法：\\n"
        f"- 🌐 搜索网络\\n"
        f"- 💻 执行命令\\n"
        f"- 📁 读写文件\\n\\n"
        f"**解决方案**：切换到支持工具的模型：\\n"
        f"`/model gemini-2.5-flash-preview`\\n\\n"
        f"或者，我可以用我现有的知识来帮助你（但可能不是最新信息）。"
    )


def create_no_tools_available_response() -> str:
    """Standard reply when tool usage is unavailable."""
    return (
        "我目前处于**纯对话模式**，无法执行系统操作。\\n\\n"
        "如需使用工具功能，请切换模型：\\n"
        "`/model gemini-2.5-flash-preview`"
    )


def should_block_output(text: str) -> tuple[bool, str]:
    """Check whether output should be blocked due to invalid fabricated URLs."""
    try:
        from nanobot.security.url_validator import extract_and_validate_urls

        url_results = extract_and_validate_urls(text)
        for result in url_results:
            if not result.is_valid:
                return True, f"检测到无效 URL: {result.original} ({result.reason})"

        return False, ""
    except ImportError:
        return False, ""


def filter_fabricated_content(text: str) -> tuple[str, list[str]]:
    """Filter/annotate invalid fabricated URLs in text output."""
    warnings = []
    filtered = text

    try:
        from nanobot.security.url_validator import extract_and_validate_urls

        url_results = extract_and_validate_urls(text)
        for result in url_results:
            if not result.is_valid:
                warnings.append(f"{result.original}: {result.reason}")
                filtered = filtered.replace(
                    result.original,
                    f"[⚠️ 链接已移除: {result.reason}]",
                )
    except ImportError:
        pass

    return filtered, warnings
