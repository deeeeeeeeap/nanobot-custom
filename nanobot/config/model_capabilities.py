"""模型能力注册表 - 明确标注每个模型的能力边界

这个模块定义了每个模型的能力，用于在运行时决定是否启用工具调用。
不支持 Function Calling 的模型将无法使用工具，只能进行纯对话。
"""

from typing import TypedDict


class ModelCapability(TypedDict, total=False):
    """模型能力定义"""
    function_calling: bool  # 是否支持函数调用
    vision: bool            # 是否支持图像输入
    streaming: bool         # 是否支持流式输出
    max_tokens: int         # 最大输出 token 数


# 模型能力注册表
# 注意：模型名称使用小写进行匹配
MODEL_CAPABILITIES: dict[str, ModelCapability] = {
    # ===== Gemini 系列 - 完全支持 =====
    "gemini-2.5-flash-preview": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "gemini-2.0-flash": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "gemini-3-flash-preview": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "gemini-pro": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    
    # ===== Claude 系列 - 完全支持 =====
    "claude-sonnet-4-5": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "claude-3-5-sonnet": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "claude-3-opus": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    
    # ===== OpenAI GPT 系列 - 完全支持 =====
    "gpt-4o": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "gpt-4-turbo": {
        "function_calling": True,
        "vision": True,
        "streaming": True,
    },
    "gpt-3.5-turbo": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    
    # ===== GPT-5 Codex 系列 - 通过 Codex CLI/反代访问，不支持 Function Calling =====
    "gpt-5.3-codex": {
        "function_calling": False,  # Codex 不支持外部工具调用
        "vision": False,
        "streaming": True,
    },
    "gpt-5-codex": {
        "function_calling": False,  # Codex 不支持外部工具调用
        "vision": False,
        "streaming": True,
    },
    
    # ===== DeepSeek 系列 - 支持 FC =====
    "deepseek-chat": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    "deepseek-coder": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    
    # ===== Groq 系列 - 部分支持 =====
    "llama-3.3-70b-versatile": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    "mixtral-8x7b": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    
    # ===== MiniMax 系列 - 通过 OpenAI 兼容 API 支持 Function Calling =====
    "minimax-m2.1": {
        "function_calling": True,  # 使用 OpenAI 兼容 API
        "vision": False,
        "streaming": True,
    },
    "minimax-text-01": {
        "function_calling": True,  # 使用 OpenAI 兼容 API
        "vision": False,
        "streaming": True,
    },
    
    # ===== 智谱 GLM 系列 =====
    "glm-4": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    
    # ===== Moonshot/Kimi 系列 =====
    "moonshot-v1": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
    "kimi-k2.5": {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    },
}


def supports_function_calling(model: str) -> bool:
    """
    检查模型是否支持 Function Calling。
    
    Args:
        model: 模型名称（可以包含前缀如 gemini/, anthropic/）
    
    Returns:
        True 如果模型支持 Function Calling，否则 False
    """
    # 清理模型名称
    model_lower = model.lower()
    
    # 移除常见前缀
    prefixes = ["gemini/", "anthropic/", "openai/", "deepseek/", "groq/", 
                "openrouter/", "hosted_vllm/", "zai/", "moonshot/", "minimax/"]
    for prefix in prefixes:
        if model_lower.startswith(prefix):
            model_lower = model_lower[len(prefix):]
            break
    
    # 精确匹配
    if model_lower in MODEL_CAPABILITIES:
        return MODEL_CAPABILITIES[model_lower].get("function_calling", False)
    
    # 模糊匹配（检查模型名是否包含在注册表的某个键中）
    for name, caps in MODEL_CAPABILITIES.items():
        if name in model_lower or model_lower in name:
            return caps.get("function_calling", False)
    
    # 基于关键词的快速判断
    # 这些通常支持
    if any(kw in model_lower for kw in ["gemini", "claude", "gpt-4", "gpt-3", "deepseek", "minimax"]):
        return True
    
    # 默认：假设支持（保守起见可改为 False）
    # 这里选择 True 是为了不阻断未知模型
    return True


def get_model_capability(model: str) -> ModelCapability:
    """
    获取模型的完整能力信息。
    
    Args:
        model: 模型名称
    
    Returns:
        模型能力字典
    """
    model_lower = model.lower()
    
    # 移除前缀
    prefixes = ["gemini/", "anthropic/", "openai/", "deepseek/", "groq/", 
                "openrouter/", "hosted_vllm/", "zai/", "moonshot/", "minimax/"]
    for prefix in prefixes:
        if model_lower.startswith(prefix):
            model_lower = model_lower[len(prefix):]
            break
    
    # 精确匹配
    if model_lower in MODEL_CAPABILITIES:
        return MODEL_CAPABILITIES[model_lower]
    
    # 模糊匹配
    for name, caps in MODEL_CAPABILITIES.items():
        if name in model_lower or model_lower in name:
            return caps
    
    # 默认能力
    return {
        "function_calling": True,
        "vision": False,
        "streaming": True,
    }
