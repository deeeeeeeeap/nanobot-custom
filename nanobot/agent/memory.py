"""Memory system for persistent agent memory."""

from __future__ import annotations

from pathlib import Path

from nanobot.memory.models import MemoryCategory
from nanobot.utils.helpers import ensure_dir


class MemoryStore:
    """Workspace-backed long-term memory and history store."""

    INDEX_CATEGORIES = [
        MemoryCategory.PREFERENCES,
        MemoryCategory.ENTITIES,
        MemoryCategory.EVENTS,
        MemoryCategory.CASES,
        MemoryCategory.PATTERNS,
    ]

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser()
        self.memory_dir = ensure_dir(self.workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self.memories_dir = ensure_dir(self.memory_dir / "memories")
        self.profile_file = self.memories_dir / "profile.md"

    def read_long_term(self) -> str:
        """Read legacy MEMORY.md (compatibility mode)."""
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        """Write legacy MEMORY.md (compatibility mode)."""
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        """Append one entry into HISTORY.md."""
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        """Build compact layered memory context for system prompt."""
        parts: list[str] = []

        # Compatibility: keep legacy MEMORY.md if present.
        long_term = self.read_long_term().strip()
        if long_term:
            parts.append("## Legacy Long-term Memory\n" + long_term)

        # Always include profile in full.
        if self.profile_file.exists():
            profile = self.profile_file.read_text(encoding="utf-8").strip()
            if profile:
                parts.append("## Profile\n" + profile)

        # L0 abstracts from categorized memory files.
        index_lines: list[str] = []
        for category in self.INDEX_CATEGORIES:
            cat_dir = self.memories_dir / category.value
            if not cat_dir.exists():
                continue
            for fp in sorted(cat_dir.glob("*.md")):
                try:
                    first_line = fp.read_text(encoding="utf-8").splitlines()[0].strip()
                except (OSError, IndexError):
                    continue
                if not first_line:
                    continue
                rel = fp.relative_to(self.workspace).as_posix()
                index_lines.append(f"- [{category.value}] {first_line} ({rel})")
        if index_lines:
            parts.append("## Memory Index (L0)\n" + "\n".join(index_lines[:80]))

        return "\n\n".join(parts)

    def get_memory_detail(self, path: str) -> str:
        """Read full memory content by workspace-relative or absolute path."""
        raw = (path or "").strip()
        if not raw:
            return ""

        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = (self.workspace / raw).resolve()
        else:
            target = target.resolve()

        try:
            target.relative_to(self.workspace.resolve())
        except ValueError:
            return ""

        if not target.exists() or not target.is_file():
            return ""
        return target.read_text(encoding="utf-8")
