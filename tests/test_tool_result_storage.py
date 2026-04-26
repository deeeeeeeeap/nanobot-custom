from pathlib import Path

import pytest
from pydantic import ValidationError

from nanobot.agent.tool_result_storage import persist_tool_result_if_needed
from nanobot.config.schema import ResultStorageConfig


def test_small_tool_result_stays_inline(tmp_path: Path) -> None:
    config = ResultStorageConfig(threshold_chars=1000)
    stored = persist_tool_result_if_needed(
        content="small output",
        tool_name="read_file",
        tool_call_id="call-1",
        workspace=tmp_path,
        config=config,
    )

    assert stored.persisted is False
    assert stored.path is None
    assert stored.content == "small output"
    assert not (tmp_path / "tool-results").exists()


def test_large_tool_result_is_persisted_inside_workspace(tmp_path: Path) -> None:
    content = "A" * 1200 + "\nTAIL"
    config = ResultStorageConfig(threshold_chars=1000, preview_chars=500)
    stored = persist_tool_result_if_needed(
        content=content,
        tool_name="exec/powershell",
        tool_call_id="call:large",
        workspace=tmp_path,
        config=config,
    )

    assert stored.persisted is True
    assert stored.path is not None
    assert stored.path.exists()
    assert stored.path.resolve().is_relative_to(tmp_path.resolve())
    assert stored.path.read_text(encoding="utf-8") == content
    assert "Full output saved to workspace path: tool-results/" in stored.content
    assert "Preview:" in stored.content
    assert "A" * 100 in stored.content
    assert "TAIL" not in stored.content


def test_large_tool_result_uses_unique_paths_for_same_call_id(tmp_path: Path) -> None:
    config = ResultStorageConfig(threshold_chars=1000, preview_chars=500)
    first = persist_tool_result_if_needed(
        content="A" * 1200,
        tool_name="exec",
        tool_call_id="call-repeat",
        workspace=tmp_path,
        config=config,
    )
    second = persist_tool_result_if_needed(
        content="B" * 1200,
        tool_name="exec",
        tool_call_id="call-repeat",
        workspace=tmp_path,
        config=config,
    )

    assert first.path is not None
    assert second.path is not None
    assert first.path != second.path
    assert first.path.read_text(encoding="utf-8") == "A" * 1200
    assert second.path.read_text(encoding="utf-8") == "B" * 1200


def test_result_storage_rejects_paths_outside_workspace() -> None:
    with pytest.raises(ValidationError):
        ResultStorageConfig(path="../outside")
