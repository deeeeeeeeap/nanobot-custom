"""实时状态汇报模块

让 nanobot 在执行工具调用时能实时反馈进度，
用户可以看到 "正在搜索..."、"正在执行命令..." 等状态。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StatusType(Enum):
    """状态类型"""
    THINKING = "thinking"       # 正在思考
    TOOL_START = "tool_start"   # 开始执行工具
    TOOL_PROGRESS = "progress"  # 工具执行进度
    TOOL_DONE = "tool_done"     # 工具执行完成
    ERROR = "error"             # 发生错误
    STREAMING = "streaming"     # 流式输出中
    COMPLETE = "complete"       # 全部完成


# 状态类型对应的图标
STATUS_ICONS: dict[StatusType, str] = {
    StatusType.THINKING: "🤔",
    StatusType.TOOL_START: "🔧",
    StatusType.TOOL_PROGRESS: "⏳",
    StatusType.TOOL_DONE: "✅",
    StatusType.ERROR: "❌",
    StatusType.STREAMING: "💬",
    StatusType.COMPLETE: "✨",
}


# 工具名称对应的友好描述
TOOL_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    # (开始描述, 完成描述)
    "web_search": ("🔍 正在搜索网络", "搜索完成"),
    "web_fetch": ("🌐 正在获取网页", "网页获取完成"),
    "exec": ("💻 正在执行命令", "命令执行完成"),
    "read_file": ("📖 正在读取文件", "文件读取完成"),
    "write_file": ("📝 正在写入文件", "文件写入完成"),
    "edit_file": ("✏️ 正在编辑文件", "文件编辑完成"),
    "list_dir": ("📁 正在列出目录", "目录列出完成"),
    "message": ("📤 正在发送消息", "消息发送完成"),
    "spawn": ("🚀 正在启动子代理", "子代理已启动"),
    "cron": ("⏰ 正在设置定时任务", "定时任务已设置"),
}


@dataclass
class StatusMessage:
    """状态消息"""
    type: StatusType
    message: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    progress: float | None = None  # 0.0 - 1.0
    details: dict[str, Any] = field(default_factory=dict)
    
    def format(self) -> str:
        """格式化为用户可读的文本"""
        icon = STATUS_ICONS.get(self.type, "📍")
        return f"{icon} {self.message}"
    
    @classmethod
    def thinking(cls) -> "StatusMessage":
        """创建思考状态"""
        return cls(type=StatusType.THINKING, message="正在思考...")
    
    @classmethod
    def tool_start(cls, tool_name: str, args: dict | None = None) -> "StatusMessage":
        """创建工具开始状态"""
        desc = TOOL_DESCRIPTIONS.get(tool_name, (f"正在执行 {tool_name}", None))
        
        # 添加额外信息
        extra = ""
        if args:
            if tool_name == "web_search" and "query" in args:
                extra = f": {args['query']}"
            elif tool_name == "exec" and "command" in args:
                cmd = args["command"]
                if len(cmd) > 30:
                    cmd = cmd[:30] + "..."
                extra = f": {cmd}"
            elif tool_name == "read_file" and "path" in args:
                extra = f": {args['path'].split('/')[-1]}"
        
        return cls(
            type=StatusType.TOOL_START,
            message=f"{desc[0]}{extra}",
            tool_name=tool_name,
            tool_args=args
        )
    
    @classmethod
    def tool_done(cls, tool_name: str, success: bool = True) -> "StatusMessage":
        """创建工具完成状态"""
        desc = TOOL_DESCRIPTIONS.get(tool_name, (None, f"{tool_name} 完成"))
        
        if success:
            return cls(
                type=StatusType.TOOL_DONE,
                message=desc[1],
                tool_name=tool_name
            )
        else:
            return cls(
                type=StatusType.ERROR,
                message=f"{tool_name} 执行失败",
                tool_name=tool_name
            )
    
    @classmethod
    def error(cls, message: str) -> "StatusMessage":
        """创建错误状态"""
        return cls(type=StatusType.ERROR, message=message)
    
    @classmethod
    def complete(cls) -> "StatusMessage":
        """创建完成状态"""
        return cls(type=StatusType.COMPLETE, message="处理完成")


class StatusReporter(ABC):
    """状态报告器基类"""
    
    @abstractmethod
    async def report(self, status: StatusMessage) -> None:
        """
        报告状态。
        
        Args:
            status: 状态消息
        """
        pass
    
    @abstractmethod
    async def finalize(self, delete_status: bool = True) -> None:
        """
        完成报告，可选删除状态消息。
        
        Args:
            delete_status: 是否删除状态消息
        """
        pass


class NullReporter(StatusReporter):
    """空报告器 - 用于不需要状态报告的场景"""
    
    async def report(self, status: StatusMessage) -> None:
        pass
    
    async def finalize(self, delete_status: bool = True) -> None:
        pass


class LogReporter(StatusReporter):
    """日志报告器 - 将状态写入日志"""
    
    def __init__(self):
        from loguru import logger
        self.logger = logger
    
    async def report(self, status: StatusMessage) -> None:
        self.logger.info(f"Status: {status.format()}")
    
    async def finalize(self, delete_status: bool = True) -> None:
        self.logger.info("Status reporting finalized")
