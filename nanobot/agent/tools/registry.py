"""Tool registry for dynamic tool management."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """Registry for agent tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        function = schema.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in stable OpenAI format order."""
        if self._cached_definitions is not None:
            return self._cached_definitions
        self._cached_definitions = sorted(
            (tool.to_schema() for tool in self._tools.values()),
            key=self._schema_name,
        )
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        """Resolve, normalize, cast, and validate one tool call."""
        if not isinstance(params, dict):
            return None, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, "
                f"got {type(params).__name__}"
            )

        tool = self._tools.get(name)
        if not tool:
            available = ", ".join(self.tool_names)
            return None, params, f"Error: Tool '{name}' not found. Available: {available}"

        normalized = self._normalize_params(name, params)
        cast_params = tool.cast_params(normalized)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        tool, prepared, error = self.prepare_call(name, params)
        if error:
            return error

        try:
            assert tool is not None
            return await tool.execute(**prepared)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return f"Error executing {name}: {exc}"

    def _normalize_params(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Normalize the known raw-JSON parameter envelope from compatible gateways."""
        import json as _json
        import re as _re

        if "raw" not in params or len(params) > 1:
            return params

        raw = params["raw"]
        if not isinstance(raw, str):
            return params

        extracted: dict[str, Any] = {}
        for match in _re.finditer(r"\{[^{}]*\}", raw):
            try:
                obj = _json.loads(match.group())
            except _json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if "content" in obj and len(obj) == 1:
                continue
            extracted.update(obj)

        if extracted:
            return extracted

        tool = self._tools.get(tool_name)
        required = tool.parameters.get("required") if tool else None
        if tool and isinstance(required, list) and required:
            main_param = required[0]
            if isinstance(main_param, str):
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
