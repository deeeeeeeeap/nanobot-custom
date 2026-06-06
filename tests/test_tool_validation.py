from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


def test_tool_cast_params_uses_schema() -> None:
    tool = SampleTool()
    params = tool.cast_params({"query": 123, "count": "2", "meta": {"tag": 9, "flags": [1]}})
    assert params == {"query": "123", "count": 2, "meta": {"tag": "9", "flags": ["1"]}}
    assert tool.validate_params(params) == []


def test_tool_cast_params_does_not_stringify_objects() -> None:
    tool = SampleTool()
    params = tool.cast_params({"query": {"bad": "shape"}, "count": "2"})
    assert params["query"] == {"bad": "shape"}
    assert any("query should be string" in err for err in tool.validate_params(params))


def test_registry_prepare_call_casts_and_validates() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    tool, params, error = reg.prepare_call("sample", {"query": 123, "count": "2"})
    assert tool is not None
    assert params["query"] == "123"
    assert params["count"] == 2
    assert error is None


def test_registry_rejects_non_object_params() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    tool, _params, error = reg.prepare_call("sample", ["bad"])  # type: ignore[arg-type]
    assert tool is None
    assert error is not None
    assert "must be a JSON object" in error


def test_registry_definitions_are_stably_sorted() -> None:
    reg = ToolRegistry()

    class ZTool(SampleTool):
        @property
        def name(self) -> str:
            return "z_tool"

    reg.register(ZTool())
    reg.register(SampleTool())
    assert [item["function"]["name"] for item in reg.get_definitions()] == ["sample", "z_tool"]
