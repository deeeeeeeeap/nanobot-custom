"""Shell execution tool."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.security.network import contains_internal_url


class ExecTool(Tool):
    """Tool to execute shell commands."""

    MAX_TIMEOUT = 600
    MAX_OUTPUT_LEN = 10000
    SUBCOMMAND_PATTERN = re.compile(r"\$\(([^()]*)\)")
    SUBCOMMAND_WHITELIST = {
        "date",
        "pwd",
        "whoami",
        "hostname",
        "cat",
        "echo",
        "grep",
        "awk",
        "sed",
        "head",
        "tail",
        "wc",
        "cut",
        "sort",
        "uniq",
        "tr",
        "id",
        "uname",
        "uptime",
        "free",
        "df",
        "du",
        "ps",
        "lsof",
        "ip",
        "ss",
        "nstat",
        "tc",
        "ping",
        "dig",
        "nslookup",
        "curl",
        "sysctl",
        "lscpu",
        "lsblk",
        "mount",
        "findmnt",
        "ls",
        "find",
        "stat",
        "file",
        "basename",
        "dirname",
        "realpath",
    }

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
            r"\b(mkfs|diskpart)\b",
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
            process = await self._create_subprocess(command, cwd)

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
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

    async def _create_subprocess(
        self,
        command: str,
        cwd: str,
    ) -> asyncio.subprocess.Process:
        windows_python = self._split_windows_multiline_python(command)
        if windows_python is not None:
            return await asyncio.create_subprocess_exec(
                *windows_python,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cwd,
            )
        return await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )

    @staticmethod
    def _split_windows_multiline_python(command: str) -> list[str] | None:
        """Bypass cmd.exe for simple multiline `python -c` commands on Windows."""
        if os.name != "nt" or "\n" not in command:
            return None
        match = re.match(
            r"^\s*(py(?:\.exe)?|python(?:\.exe)?|python3(?:\.exe)?)\s+-c\s+(.+?)\s*$",
            command,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        executable, code = match.groups()
        code = code.strip()
        if len(code) >= 2 and code[0] in {"'", '"'} and code[-1] == code[0]:
            code = code[1:-1]
        if not code:
            return None
        return [executable, "-c", code]

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for i, pattern in enumerate(self.deny_patterns):
            if re.search(pattern, lower):
                return f"Error: Command blocked by safety guard (rule DENY-{i}: dangerous pattern '{pattern}')"

        subcommand_error = self._validate_subcommand_substitution(cmd, cwd)
        if subcommand_error:
            return subcommand_error

        for i, pattern in enumerate(self.injection_patterns):
            if re.search(pattern, cmd):
                return f"Error: Command blocked by safety guard (rule INJ-{i}: injection pattern '{pattern}')"

        if self.allow_patterns and not any(re.search(p, lower) for p in self.allow_patterns):
            return "Error: Command blocked by safety guard (rule ALLOWLIST: not in allowlist)"

        if contains_internal_url(cmd):
            return "Error: Command blocked by safety guard (internal/private URL detected)"

        if self.restrict_to_workspace:
            if re.search(r"(^|[\\/])\.\.([\\/]|$)", cmd):
                return "Error: Command blocked by safety guard (rule WORKSPACE-TRAVERSAL: path traversal detected)"

            cwd_path = Path(cwd).resolve()
            for raw in self._extract_workspace_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    path = Path(expanded).expanduser().resolve()
                except (OSError, RuntimeError, ValueError):
                    continue

                if path.is_absolute() and cwd_path not in path.parents and path != cwd_path:
                    return (
                        "Error: Command blocked by safety guard "
                        f"(rule WORKSPACE-PATH: '{raw.strip()}' outside working dir)"
                    )

        return None

    @staticmethod
    def _extract_workspace_paths(command: str) -> list[str]:
        """Extract absolute and home-relative path tokens from a command string."""
        win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", command)
        posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", command)
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command)
        return win_paths + posix_paths + home_paths

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
