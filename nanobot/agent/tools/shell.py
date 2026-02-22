"""Shell execution tool."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""

    MAX_TIMEOUT = 600
    MAX_OUTPUT_LEN = 10000
    SUBCOMMAND_PATTERN = re.compile(r"\$\(([^()]*)\)")
    SUBCOMMAND_WHITELIST = {"date", "pwd", "whoami", "hostname", "cat", "echo"}

    def __init__(
        self,
        timeout: int = 120,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            # rm -rf / del /f 已移除：bot 需要清理临时文件的权限
            r"\b(mkfs|diskpart)\b",
            # Block standalone "format" command only, allow flags like "--format=json".
            r"(?:^|[;&|]\s*)format(?:\s|$)",
            r"\bdd\s+if=",
            r">\s*/dev/sd",
            r"\b(shutdown|reboot|poweroff)\b",
            r":\(\)\s*\{.*\};\s*:",
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.injection_patterns = [
            r"`[^`]+`",
            r"\x00",
            r"[\r\n]",
        ]

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120, max 600).",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        effective_timeout = max(1, min(timeout or self.timeout, self.MAX_TIMEOUT))

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                # Ensure process resources are reclaimed after kill.
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return (
                    f"Error: command timed out after {effective_timeout}s. "
                    f"Use timeout parameter (max {self.MAX_TIMEOUT})."
                )

            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"
            if len(result) > self.MAX_OUTPUT_LEN:
                result = (
                    result[: self.MAX_OUTPUT_LEN]
                    + f"\n... (truncated, {len(result) - self.MAX_OUTPUT_LEN} more chars)"
                )
            return result
        except (OSError, ValueError, UnicodeError) as e:
            return f"Error executing command: {e}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        subcommand_error = self._validate_subcommand_substitution(cmd, cwd)
        if subcommand_error:
            return subcommand_error

        for pattern in self.injection_patterns:
            if re.search(pattern, cmd):
                return "Error: Command blocked by safety guard (command injection pattern detected)"

        if self.allow_patterns and not any(re.search(p, lower) for p in self.allow_patterns):
            return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if re.search(r"(^|[\\/])\.\.([\\/]|$)", cmd):
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()
            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except (OSError, RuntimeError, ValueError):
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    def _validate_subcommand_substitution(self, command: str, cwd: str) -> str | None:
        """Allow only a narrow `$()` subset to reduce false positives without opening RCE paths."""
        if "$(" not in command:
            return None
        if re.search(r"\$\([^()]*\$\(", command):
            return "Error: Command blocked by safety guard (nested subcommand substitution is not allowed)"

        matches = self.SUBCOMMAND_PATTERN.findall(command)
        if not matches or command.count("$(") != len(matches):
            return "Error: Command blocked by safety guard (malformed subcommand substitution)"

        for raw_inner in matches:
            inner = raw_inner.strip()
            if not inner:
                return "Error: Command blocked by safety guard (empty subcommand substitution)"
            if any(token in inner for token in (";", "&&", "||", "|", "`", ">", "<", "\r", "\n")):
                return "Error: Command blocked by safety guard (unsafe subcommand composition)"

            parts = inner.split()
            subcmd = parts[0].lower()
            if subcmd not in self.SUBCOMMAND_WHITELIST:
                return "Error: Command blocked by safety guard (subcommand not in allowlist)"

            if subcmd == "cat":
                if len(parts) != 2:
                    return "Error: Command blocked by safety guard (cat subcommand expects one file path)"
                path_token = parts[1].strip("'\"")
                if not path_token or path_token.startswith("-"):
                    return "Error: Command blocked by safety guard (invalid cat path)"
                path_obj = Path(path_token)
                if path_obj.is_absolute() or any(part == ".." for part in path_obj.parts):
                    return "Error: Command blocked by safety guard (cat path must stay relative)"
                if self.restrict_to_workspace:
                    cwd_path = Path(cwd).resolve()
                    resolved = (cwd_path / path_obj).resolve()
                    if cwd_path not in resolved.parents and resolved != cwd_path:
                        return "Error: Command blocked by safety guard (cat path outside working dir)"

        return None
