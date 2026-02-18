from pathlib import Path

from nanobot.agent.tools.filesystem import MAX_FILE_BYTES, EditFileTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.shell import ExecTool


async def test_exec_tool_blocks_command_injection_pattern() -> None:
    tool = ExecTool()
    result = await tool.execute("echo $(whoami)")
    assert "command injection pattern" in result.lower()


async def test_exec_blocks_mkfs() -> None:
    tool = ExecTool()
    result = await tool.execute("mkfs.ext4 /dev/sda1")
    assert "dangerous pattern" in result.lower()


async def test_exec_blocks_shutdown() -> None:
    tool = ExecTool()
    result = await tool.execute("shutdown -h now")
    assert "dangerous pattern" in result.lower()


async def test_exec_blocks_fork_bomb() -> None:
    tool = ExecTool()
    result = await tool.execute(":(){ :|:& };:")
    assert "dangerous pattern" in result.lower()


async def test_exec_respects_workspace_restriction(tmp_path: Path) -> None:
    outside = (tmp_path.parent / "outside.txt").resolve()
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True)
    result = await tool.execute(f"Get-Content {outside}")
    assert "outside working dir" in result.lower()


async def test_read_file_tool_blocks_path_traversal(tmp_path: Path) -> None:
    tool = ReadFileTool(allowed_dir=tmp_path)
    result = await tool.execute("../outside.txt")
    assert "path traversal" in result.lower() or "outside allowed directory" in result.lower()


async def test_read_file_tool_rejects_large_files(tmp_path: Path) -> None:
    target = tmp_path / "big.txt"
    target.write_bytes(b"a" * (MAX_FILE_BYTES + 1))
    tool = ReadFileTool(allowed_dir=tmp_path)
    result = await tool.execute(str(target))
    assert "file too large" in result.lower()


async def test_write_file_tool_rejects_large_content(tmp_path: Path) -> None:
    tool = WriteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(str(tmp_path / "big.txt"), "a" * (MAX_FILE_BYTES + 1))
    assert "content too large" in result.lower()


async def test_edit_file_rejects_ambiguous_match(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    tool = EditFileTool(allowed_dir=tmp_path)
    result = await tool.execute(str(file_path), old_text="alpha", new_text="gamma")
    assert "appears 2 times" in result.lower()


def test_message_tool_validates_channel_and_chat_id_format() -> None:
    tool = MessageTool()
    errors = tool.validate_params(
        {
            "content": "hello",
            "channel": "Invalid-Channel",
            "chat_id": "bad chat id",
        }
    )
    joined = "; ".join(errors)
    assert "channel" in joined
    assert "chat_id" in joined
