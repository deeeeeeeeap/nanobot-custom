from pathlib import Path

from nanobot.agent.tools.filesystem import MAX_FILE_BYTES, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.shell import ExecTool


async def test_exec_tool_blocks_command_injection_pattern() -> None:
    tool = ExecTool()
    result = await tool.execute("echo $(whoami)")
    assert "command injection pattern" in result.lower()


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
