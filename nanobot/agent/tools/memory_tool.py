"""Memory tool for structured memory management."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.search.indexer import Indexer


class MemoryTool(Tool):
    """Allow the agent to read/write core memory files."""

    ALLOWED_FILES = {"MEMORY.md"}

    def __init__(
        self,
        memory_store: MemoryStore,
        workspace: Path,
        indexer: "Indexer | None" = None,
    ):
        self._memory = memory_store
        self._workspace = workspace
        self._indexer = indexer

    def set_indexer(self, indexer: "Indexer | None") -> None:
        self._indexer = indexer

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Manage structured memory files. Supports read, write, append, list, and log actions "
            "for MEMORY.md and workspace memory logs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "append", "list", "log"],
                    "description": "Operation type",
                },
                "file": {
                    "type": "string",
                    "description": "Target file name: MEMORY.md",
                },
                "content": {
                    "type": "string",
                    "description": "Content for write/append/log actions",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        file: str | None = None,
        content: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "list":
            return self._list_files()
        if action == "log":
            return self._log_today(content)
        if action == "read":
            return self._read_file(file)
        if action == "write":
            return self._write_file(file, content)
        if action == "append":
            return self._append_file(file, content)
        return f"Error: unknown action '{action}'"

    def _list_files(self) -> str:
        parts = ["Memory files:"]
        status = "exists" if self._memory.memory_file.exists() else "missing"
        parts.append(f"- MEMORY.md: {status}")

        memory_dir = self._workspace / "memory"
        if memory_dir.exists():
            extra = sorted(p.name for p in memory_dir.glob("*.md") if p.is_file())
            if extra:
                parts.append("memory/*.md:")
                parts.extend(f"- {name}" for name in extra)
        return "\n".join(parts)

    def _read_file(self, file: str | None) -> str:
        if not file:
            return "Error: file is required"

        if file == "MEMORY.md":
            content = self._memory.read_long_term()
            return content if content else "(MEMORY.md is empty)"

        if file not in self.ALLOWED_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_FILES))
            return f"Error: file '{file}' is not allowed. Allowed: {allowed}"

        path = self._workspace / file
        if not path.exists():
            return f"({file} does not exist)"
        return path.read_text(encoding="utf-8")

    def _write_file(self, file: str | None, content: str | None) -> str:
        if not file:
            return "Error: file is required"
        if content is None:
            return "Error: content is required"
        if file not in self.ALLOWED_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_FILES))
            return f"Error: file '{file}' is not allowed. Allowed: {allowed}"

        if file == "MEMORY.md":
            self._memory.write_long_term(content)
        else:
            path = self._workspace / file
            path.write_text(content, encoding="utf-8")

        self._index_file(file)
        logger.info(f"Memory write: {file} ({len(content)} chars)")
        return f"OK: wrote {file} ({len(content)} chars)"

    def _append_file(self, file: str | None, content: str | None) -> str:
        if not file:
            return "Error: file is required"
        if not content:
            return "Error: content is required"
        if file not in self.ALLOWED_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_FILES))
            return f"Error: file '{file}' is not allowed. Allowed: {allowed}"

        if file == "MEMORY.md":
            existing = self._memory.read_long_term()
            merged = f"{existing}\n{content}" if existing else content
            self._memory.write_long_term(merged)
        else:
            path = self._workspace / file
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            merged = f"{existing}\n{content}" if existing else content
            path.write_text(merged, encoding="utf-8")

        self._index_file(file)
        logger.info(f"Memory append: {file}")
        return f"OK: appended to {file}"

    def _log_today(self, content: str | None) -> str:
        if not content:
            return "Error: content is required"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._memory.append_history(f"[{timestamp}] {content}")
        return "OK: log entry added to HISTORY.md"

    def _index_file(self, file: str) -> None:
        if self._indexer is None:
            return
        target = self._workspace / file
        if file == "MEMORY.md":
            target = self._workspace / "memory" / "MEMORY.md"
        try:
            self._indexer.index_single(target)
        except Exception as e:  # pragma: no cover - non-critical follow-up path
            logger.warning(f"Memory index update failed for {target}: {e}")
