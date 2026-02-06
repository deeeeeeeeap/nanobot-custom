"""Telegram 状态报告器

通过编辑 Telegram 消息实现实时状态更新，
无需发送大量新消息，用户体验更好。
"""

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.agent.status import StatusMessage, StatusReporter, StatusType

if TYPE_CHECKING:
    from telegram import Bot


class TelegramStatusReporter(StatusReporter):
    """
    Telegram 状态报告器。
    
    通过编辑单条消息来显示状态更新，避免消息刷屏。
    当所有操作完成后，可选择删除状态消息。
    """
    
    # 防止编辑过于频繁的最小间隔（秒）
    MIN_EDIT_INTERVAL = 0.5
    
    def __init__(self, bot: "Bot", chat_id: int):
        """
        初始化 Telegram 状态报告器。
        
        Args:
            bot: Telegram Bot 实例
            chat_id: 聊天 ID
        """
        self.bot = bot
        self.chat_id = chat_id
        self.status_message_id: int | None = None
        self.last_edit_time: float = 0
        self.status_history: list[str] = []  # 记录状态历史
        self.current_status: str = ""
    
    async def report(self, status: StatusMessage) -> None:
        """
        报告状态。
        
        首次调用时发送新消息，后续调用编辑该消息。
        """
        formatted = status.format()
        
        # 避免重复更新相同状态
        if formatted == self.current_status:
            return
        
        self.current_status = formatted
        
        # 构建显示文本（包含历史）
        if status.type == StatusType.TOOL_DONE:
            # 工具完成时添加到历史
            self.status_history.append(formatted)
        
        display_text = self._build_display_text(status)
        
        try:
            # 限制编辑频率
            now = asyncio.get_event_loop().time()
            if now - self.last_edit_time < self.MIN_EDIT_INTERVAL:
                await asyncio.sleep(self.MIN_EDIT_INTERVAL - (now - self.last_edit_time))
            
            if self.status_message_id is None:
                # 发送新的状态消息
                msg = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=display_text,
                    parse_mode="HTML"
                )
                self.status_message_id = msg.message_id
                logger.debug(f"Status message created: {self.status_message_id}")
            else:
                # 编辑现有消息
                await self.bot.edit_message_text(
                    text=display_text,
                    chat_id=self.chat_id,
                    message_id=self.status_message_id,
                    parse_mode="HTML"
                )
            
            self.last_edit_time = asyncio.get_event_loop().time()
            
        except Exception as e:
            # 编辑失败时记录日志但不中断流程
            logger.warning(f"Failed to update status message: {e}")
    
    def _build_display_text(self, current: StatusMessage) -> str:
        """
        构建显示文本。
        
        显示当前状态，以及已完成的工具（简化显示）。
        """
        lines = []
        
        # 显示已完成的步骤（最多 5 个）
        if self.status_history:
            recent = self.status_history[-5:]
            for item in recent:
                lines.append(f"<s>{item}</s>")  # 删除线表示已完成
        
        # 当前状态
        if current.type not in (StatusType.COMPLETE,):
            lines.append(f"\n<b>{current.format()}</b>")
        
        return "\n".join(lines) if lines else current.format()
    
    async def finalize(self, delete_status: bool = True) -> None:
        """
        完成报告。
        
        Args:
            delete_status: 是否删除状态消息
        """
        if self.status_message_id is None:
            return
        
        try:
            if delete_status:
                # 删除状态消息
                await self.bot.delete_message(
                    chat_id=self.chat_id,
                    message_id=self.status_message_id
                )
                logger.debug(f"Status message deleted: {self.status_message_id}")
            else:
                # 更新为完成状态
                await self.bot.edit_message_text(
                    text="✨ 处理完成",
                    chat_id=self.chat_id,
                    message_id=self.status_message_id,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"Failed to finalize status message: {e}")
        finally:
            self.status_message_id = None
            self.status_history.clear()
            self.current_status = ""
