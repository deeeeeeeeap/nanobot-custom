import asyncio
from pathlib import Path

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.filesystem import (
    MAX_FILE_BYTES,
    EditFileTool,
    ReadFileTool,
    WriteFileTool,
)
from nanobot.agent.tools.web import _get_with_safe_redirects
from nanobot.agent.tools.memory_tool import MemoryTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.security.network import validate_resolved_url


async def test_exec_tool_blocks_command_injection_pattern() -> None:
    tool = ExecTool()
    result = await tool.execute("echo `whoami`")
    assert "injection pattern" in result.lower()


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


async def test_exec_blocks_standalone_format_command() -> None:
    tool = ExecTool()
    result = await tool.execute("format /q")
    assert "dangerous pattern" in result.lower()


def test_exec_allows_whitelisted_subcommand_substitution() -> None:
    tool = ExecTool()
    assert tool._guard_command("echo $(whoami)", cwd=".") is None


def test_exec_blocks_non_whitelisted_subcommand_substitution() -> None:
    tool = ExecTool()
    result = tool._guard_command("echo $(rm -rf /)", cwd=".")
    assert result is not None
    assert "subcommand" in result.lower()


def test_exec_blocks_nested_subcommand_substitution() -> None:
    tool = ExecTool()
    result = tool._guard_command("echo $(echo $(date))", cwd=".")
    assert result is not None
    assert "nested subcommand" in result.lower()


def test_exec_blocks_unsafe_subcommand_composition() -> None:
    tool = ExecTool()
    result = tool._guard_command("echo $(date; whoami)", cwd=".")
    assert result is not None
    assert "unsafe subcommand composition" in result.lower()


def test_ssrf_blocks_ipv6_mapped_loopback() -> None:
    ok, detail = validate_resolved_url("http://[::ffff:127.0.0.1]/metadata")
    assert not ok
    assert "private address" in detail.lower()


async def test_web_fetch_validates_redirect_before_following(monkeypatch) -> None:
    calls: list[str] = []

    def fake_validate(url: str):
        if "169.254.169.254" in url:
            return False, "metadata target"
        return True, ""

    class FakeClient:
        async def get(self, url, *, headers=None, follow_redirects=False):
            calls.append(url)
            return httpx.Response(
                302,
                headers={"location": "http://169.254.169.254/latest"},
                request=httpx.Request("GET", url),
            )

    import httpx

    monkeypatch.setattr("nanobot.agent.tools.web.validate_url_target", fake_validate)
    response, error = await _get_with_safe_redirects(FakeClient(), "https://example.com")

    assert response is None
    assert "redirect blocked" in error.lower()
    assert calls == ["https://example.com"]


async def test_exec_timeout_waits_for_process_exit(monkeypatch) -> None:
    class _FakeProcess:
        def __init__(self) -> None:
            self.killed = False
            self.wait_called = False
            self.returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self):
            self.wait_called = True
            return 0

    proc = _FakeProcess()
    wait_calls = {"n": 0}

    async def _fake_create_subprocess_shell(*args, **kwargs):
        return proc

    async def _fake_wait_for(awaitable, timeout):
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr("nanobot.agent.tools.shell.asyncio.create_subprocess_shell", _fake_create_subprocess_shell)
    monkeypatch.setattr("nanobot.agent.tools.shell.asyncio.wait_for", _fake_wait_for)

    tool = ExecTool(timeout=1)
    result = await tool.execute("echo hi")
    assert "timed out" in result.lower()
    assert proc.killed
    assert proc.wait_called


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


async def test_edit_file_not_found_reports_best_match(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("alpha beta gamma\n", encoding="utf-8")
    tool = EditFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        str(file_path),
        old_text="alpha beta delta\n",
        new_text="alpha beta omega\n",
    )
    assert "best match" in result.lower()
    assert "old_text (provided)" in result


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


async def test_memory_tool_rejects_non_memory_file(tmp_path: Path) -> None:
    tool = MemoryTool(memory_store=MemoryStore(tmp_path), workspace=tmp_path)
    result = await tool.execute(action="read", file="USER.md")
    assert "not allowed" in result.lower()
    assert "MEMORY.md" in result


def test_memory_tool_declares_memory_only(tmp_path: Path) -> None:
    tool = MemoryTool(memory_store=MemoryStore(tmp_path), workspace=tmp_path)
    assert tool.ALLOWED_FILES == {"MEMORY.md"}
    assert "USER.md" not in tool.description
    assert tool.parameters["properties"]["file"]["description"] == "Target file name: MEMORY.md"
