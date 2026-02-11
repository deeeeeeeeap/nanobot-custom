"""Tool registry for dynamic tool management."""

from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    """
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
    
    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
    
    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]
    
    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.
        
        Args:
            name: Tool name.
            params: Tool parameters.
        
        Returns:
            Tool execution result as string.
        
        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        # 定制：参数格式兼容处理
        # Antigravity 网关可能返回 {"raw": "{...}{...}"} 格式
        params = self._normalize_params(name, params)

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            return await tool.execute(**params)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
    
    def _normalize_params(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        规范化工具参数，兼容不同 API 网关的格式差异。
        
        处理 Antigravity 网关的特殊格式：
        {"raw": '{"content":"..."}{"command":"uptime"}'}
        → {"command": "uptime"}
        """
        import json as _json
        import re as _re
        
        if "raw" not in params or len(params) > 1:
            return params
        
        raw = params["raw"]
        if not isinstance(raw, str):
            return params
        
        # 尝试从 raw 字符串中提取所有 JSON 对象
        extracted = {}
        # 匹配所有 {...} 块
        for match in _re.finditer(r'\{[^{}]*\}', raw):
            try:
                obj = _json.loads(match.group())
                # 跳过 content 字段（那是状态描述，不是工具参数）
                if "content" in obj and len(obj) == 1:
                    continue
                extracted.update(obj)
            except _json.JSONDecodeError:
                continue
        
        if extracted:
            from loguru import logger
            logger.debug(f"工具参数规范化: {tool_name} raw → {extracted}")
            return extracted
        
        # 如果无法解析，尝试把 raw 当作主参数
        tool = self._tools.get(tool_name)
        if tool and tool.parameters.get("required"):
            main_param = tool.parameters["required"][0]
            return {main_param: raw}
        
        return params
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools
