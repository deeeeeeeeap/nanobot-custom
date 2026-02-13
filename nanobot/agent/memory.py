"""Memory system for persistent agent memory."""

from pathlib import Path

from nanobot.utils.helpers import ensure_dir


class MemoryStore:
    """
    双层记忆系统。
    
    - MEMORY.md: 长期事实（用户偏好、项目上下文、关键信息）。始终加载到上下文。
    - HISTORY.md: 追加式事件日志（对话摘要）。不加载到上下文，通过 grep 检索。
    """
    
    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
    
    def read_long_term(self) -> str:
        """读取长期记忆（MEMORY.md）。"""
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""
    
    def write_long_term(self, content: str) -> None:
        """写入长期记忆（MEMORY.md）。"""
        self.memory_file.write_text(content, encoding="utf-8")
    
    def append_history(self, entry: str) -> None:
        """追加一条事件到历史日志（HISTORY.md）。"""
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")
    
    def get_memory_context(self) -> str:
        """
        获取记忆上下文（只加载长期记忆）。
        
        HISTORY.md 不加载到上下文，需要时通过 exec 工具的 grep 搜索。
        """
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""
