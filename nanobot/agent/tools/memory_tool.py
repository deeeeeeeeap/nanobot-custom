"""Memory tool for structured memory management."""

from typing import Any
from pathlib import Path

from nanobot.agent.tools.base import Tool
from nanobot.agent.memory import MemoryStore
from loguru import logger


class MemoryTool(Tool):
    """结构化记忆管理工具，让 Agent 可以主动读写记忆文件。"""
    
    # 允许操作的引导文件
    ALLOWED_FILES = {"IDENTITY.md", "USER.md", "SOUL.md", "MEMORY.md"}
    
    def __init__(self, memory_store: MemoryStore, workspace: Path):
        self._memory = memory_store
        self._workspace = workspace
    
    @property
    def name(self) -> str:
        return "memory"
    
    @property
    def description(self) -> str:
        return (
            "管理结构化记忆。支持读取、写入、追加记忆文件。"
            "用于记录用户偏好(USER.md)、经验教训(MEMORY.md)、今日日记等。"
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "append", "list", "log"],
                    "description": (
                        "操作类型：read=读取文件, write=覆盖写入, "
                        "append=追加内容, list=列出所有记忆文件, "
                        "log=追加到今日日记"
                    )
                },
                "file": {
                    "type": "string",
                    "description": (
                        "目标文件名：IDENTITY.md, USER.md, SOUL.md, MEMORY.md。"
                        "log 操作不需要此参数。"
                    )
                },
                "content": {
                    "type": "string",
                    "description": "write/append/log 操作的内容"
                }
            },
            "required": ["action"]
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
        elif action == "log":
            return self._log_today(content)
        elif action == "read":
            return self._read_file(file)
        elif action == "write":
            return self._write_file(file, content)
        elif action == "append":
            return self._append_file(file, content)
        else:
            return f"Error: 未知操作 '{action}'，支持: read, write, append, list, log"
    
    def _list_files(self) -> str:
        """列出所有记忆相关文件。"""
        parts = ["📂 记忆文件列表：\n"]
        
        # 引导文件
        for f in self.ALLOWED_FILES:
            path = self._workspace / f
            status = "✅ 存在" if path.exists() else "❌ 不存在"
            parts.append(f"  - {f}: {status}")
        
        # 日记文件
        daily_files = self._memory.list_memory_files()
        if daily_files:
            parts.append(f"\n📅 日记文件（最近 {min(len(daily_files), 10)} 个）：")
            for f in daily_files[:10]:
                parts.append(f"  - {f.name}")
        
        return "\n".join(parts)
    
    def _read_file(self, file: str | None) -> str:
        """读取指定记忆文件。"""
        if not file:
            return "Error: 请指定文件名（如 USER.md, MEMORY.md）"
        
        if file == "MEMORY.md":
            content = self._memory.read_long_term()
            return content if content else "（MEMORY.md 为空）"
        
        if file not in self.ALLOWED_FILES:
            return f"Error: 不允许读取 '{file}'，可用: {', '.join(self.ALLOWED_FILES)}"
        
        path = self._workspace / file
        if not path.exists():
            return f"（{file} 不存在）"
        
        return path.read_text(encoding="utf-8")
    
    def _write_file(self, file: str | None, content: str | None) -> str:
        """覆盖写入记忆文件。"""
        if not file:
            return "Error: 请指定文件名"
        if not content:
            return "Error: 请提供内容"
        if file not in self.ALLOWED_FILES:
            return f"Error: 不允许写入 '{file}'，可用: {', '.join(self.ALLOWED_FILES)}"
        
        if file == "MEMORY.md":
            self._memory.write_long_term(content)
        else:
            path = self._workspace / file
            path.write_text(content, encoding="utf-8")
        
        logger.info(f"Memory: 写入 {file} ({len(content)} 字符)")
        return f"✅ 已写入 {file}（{len(content)} 字符）"
    
    def _append_file(self, file: str | None, content: str | None) -> str:
        """追加内容到记忆文件。"""
        if not file:
            return "Error: 请指定文件名"
        if not content:
            return "Error: 请提供内容"
        if file not in self.ALLOWED_FILES:
            return f"Error: 不允许追加 '{file}'，可用: {', '.join(self.ALLOWED_FILES)}"
        
        if file == "MEMORY.md":
            existing = self._memory.read_long_term()
            self._memory.write_long_term(existing + "\n" + content if existing else content)
        else:
            path = self._workspace / file
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(existing + "\n" + content, encoding="utf-8")
        
        logger.info(f"Memory: 追加到 {file}")
        return f"✅ 已追加到 {file}"
    
    def _log_today(self, content: str | None) -> str:
        """追加到今日日记。"""
        if not content:
            return "Error: 请提供日记内容"
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"- [{timestamp}] {content}"
        self._memory.append_today(entry)
        
        logger.info(f"Memory: 今日日记追加")
        return f"✅ 已记录到今日日记"
