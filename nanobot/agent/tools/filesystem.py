"""File system tools: read, write, edit."""

import difflib
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool

MAX_FILE_BYTES = 1_000_000
DEFAULT_READ_LIMIT = 2000


def _resolve_path(path: str, allowed_dir: Path | None = None) -> Path:
    """Resolve path and optionally enforce directory restriction."""
    raw = Path(path).expanduser()
    if allowed_dir and any(part == ".." for part in raw.parts):
        raise PermissionError("Path traversal is not allowed")

    resolved = raw.resolve()
    if allowed_dir:
        allowed_root = allowed_dir.resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError as e:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_root}") from e
    return resolved


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path. Supports offset and limit for pagination."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed, default 1)",
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default 2000)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, offset: int = 1, limit: int | None = None, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"
            if file_path.stat().st_size > MAX_FILE_BYTES:
                return f"Error: File too large (> {MAX_FILE_BYTES} bytes): {path}"

            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            total = len(lines)

            if total == 0:
                return f"(Empty file: {path})"

            if offset < 1:
                offset = 1
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            page_limit = limit if limit is not None else DEFAULT_READ_LIMIT
            if page_limit < 1:
                page_limit = DEFAULT_READ_LIMIT

            start = offset - 1
            end = min(start + page_limit, total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(lines[start:end])]

            result = "\n".join(numbered)
            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file - {total} lines total)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except (OSError, UnicodeError, ValueError) as e:
            return f"Error reading file: {e}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            payload = content.encode("utf-8")
            if len(payload) > MAX_FILE_BYTES:
                return f"Error: Content too large (> {MAX_FILE_BYTES} bytes)"

            file_path = _resolve_path(path, self._allowed_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(payload)} bytes to {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except (OSError, UnicodeError, ValueError) as e:
            return f"Error writing file: {e}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "Supports fallback matching for minor whitespace and line-ending differences."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to edit",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace",
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace with",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if file_path.stat().st_size > MAX_FILE_BYTES:
                return f"Error: File too large (> {MAX_FILE_BYTES} bytes): {path}"

            content = file_path.read_text(encoding="utf-8")
            match, count = self._find_match(content, old_text)
            if match is None:
                return self._not_found_message(old_text, content, path)

            if count > 1 and not replace_all:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(match, new_text) if replace_all else content.replace(match, new_text, 1)
            if len(new_content.encode("utf-8")) > MAX_FILE_BYTES:
                return f"Error: Updated content too large (> {MAX_FILE_BYTES} bytes)"
            file_path.write_text(new_content, encoding="utf-8")
            return f"Successfully edited {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except (OSError, UnicodeError, ValueError) as e:
            return f"Error editing file: {e}"

    @staticmethod
    def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
        """Find an exact or whitespace-tolerant match for old_text in content."""
        if not old_text:
            return None, 0

        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        normalized_old = old_text.replace("\r\n", "\n").replace("\r", "\n")

        if normalized_old in normalized_content:
            return normalized_old, normalized_content.count(normalized_old)

        old_lines = normalized_old.splitlines()
        if not old_lines:
            return None, 0

        stripped_old = [line.strip() for line in old_lines]
        content_lines = normalized_content.splitlines()
        if len(content_lines) < len(old_lines):
            return None, 0

        candidates: list[str] = []
        window = len(old_lines)
        for i in range(len(content_lines) - window + 1):
            chunk = content_lines[i : i + window]
            if [line.strip() for line in chunk] == stripped_old:
                candidates.append("\n".join(chunk))

        if candidates:
            return candidates[0], len(candidates)
        return None, 0

    @staticmethod
    def _not_found_message(old_text: str, content: str, path: str) -> str:
        """Build a helpful error with a nearest-match diff when old_text is missing."""
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        if not lines or not old_lines:
            return f"Error: old_text not found in {path}. Verify the file content."

        window = len(old_lines)
        best_ratio, best_start = 0.0, 0
        old_score = "\n".join(line.strip() for line in old_lines)
        max_start = max(1, len(lines) - window + 1)
        for i in range(max_start):
            chunk = lines[i : i + window]
            chunk_score = "\n".join(line.strip() for line in chunk)
            ratio = difflib.SequenceMatcher(None, old_score, chunk_score).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        chunk = lines[best_start : best_start + window]
        diff = "\n".join(
            difflib.unified_diff(
                old_lines,
                chunk,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            )
        )
        if not diff:
            diff = "(no diff available)"

        return (
            f"Error: old_text not found in {path}.\n"
            f"Best match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        )


class ListDirTool(Tool):
    """Tool to list directory contents."""

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recursively list all files (default false)",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum entries to return (default 200)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(
        self, path: str, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            dir_path = _resolve_path(path, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dir_path.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dir_path)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dir_path.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        prefix = "[D]" if item.is_dir() else "[F]"
                        items.append(f"{prefix} {item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except (OSError, UnicodeError, ValueError) as e:
            return f"Error listing directory: {e}"

