"""Cron tool for scheduling reminders and tasks."""

from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule


class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""
    
    def __init__(self, cron_service: CronService):
        self._cron = cron_service
        self._channel = ""
        self._chat_id = ""
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """设置当前会话上下文，用于消息投递。"""
        self._channel = channel
        self._chat_id = chat_id
    
    @property
    def name(self) -> str:
        return "cron"
    
    @property
    def description(self) -> str:
        return (
            "Schedule reminders or agent tasks. "
            "mode='remind' sends a static message; "
            "mode='agent' makes the agent execute the prompt with full tool access "
            "(weather, exec, web_search, etc.) and send results to the user."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform"
                },
                "message": {
                    "type": "string",
                    "description": (
                        "内容文本。mode=remind 时为直接发送的提醒文字；"
                        "mode=agent 时为要求 Agent 执行的指令"
                        "（Agent 将用完整工具链处理并将结果发送给用户）"
                    )
                },
                "mode": {
                    "type": "string",
                    "enum": ["remind", "agent"],
                    "description": (
                        "任务模式。remind=发送静态文本提醒（默认）；"
                        "agent=由 Agent 完整执行指令（可调用 weather/exec/web_search 等工具）"
                    )
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)"
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for scheduled tasks)"
                },
                "timezone": {
                    "type": "string",
                    "description": "时区，如 'Asia/Shanghai'。用于 cron_expr 的时间计算，默认 UTC"
                },
                "job_id": {
                    "type": "string",
                    "description": "Job ID (for remove)"
                }
            },
            "required": ["action"]
        }
    
    async def execute(
        self,
        action: str,
        message: str = "",
        mode: str = "remind",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        timezone: str | None = None,
        job_id: str | None = None,
        **kwargs: Any
    ) -> str:
        if action == "add":
            return self._add_job(message, mode, every_seconds, cron_expr, timezone)
        elif action == "list":
            return self._list_jobs()
        elif action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"
    
    def _add_job(
        self,
        message: str,
        mode: str,
        every_seconds: int | None,
        cron_expr: str | None,
        timezone: str | None,
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"
        
        # 构建调度计划
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=timezone)
        else:
            return "Error: either every_seconds or cron_expr is required"
        
        # 根据 mode 决定 payload.kind
        payload_kind = "agent_turn" if mode == "agent" else "system_event"
        mode_label = "🤖 Agent 模式" if mode == "agent" else "📨 提醒模式"
        
        # 定制：验证 agent 模式的 message 不是工具调用语法
        if mode == "agent":
            import re
            # 检测 exec(...), weather(...) 等工具调用格式
            tool_call_match = re.match(
                r'^(exec|cron|weather|web_search|web_fetch|message)\s*\(',
                message.strip()
            )
            if tool_call_match:
                # 尝试从工具调用中提取实际命令
                cmd_match = re.search(r"command=['\"](.+?)['\"]", message)
                if cmd_match:
                    extracted = cmd_match.group(1)
                    message = f"执行命令 {extracted} 并报告结果"
                else:
                    message = f"请执行以下操作并报告结果: {message}"
                from loguru import logger
                logger.warning(f"Cron agent message 格式已纠正: {message[:60]}")
        
        job = self._cron.add_job(
            name=message[:30],
            schedule=schedule,
            message=message,
            deliver=True,
            channel=self._channel,
            to=self._chat_id,
            payload_kind=payload_kind,
        )
        return f"✅ 已创建定时任务 [{mode_label}]\n名称: {job.name}\nID: {job.id}"
    
    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "当前没有定时任务。"
        lines = []
        for j in jobs:
            mode_icon = "🤖" if j.payload.kind == "agent_turn" else "📨"
            tz_info = f" ({j.schedule.tz})" if j.schedule.tz else ""
            if j.schedule.kind == "cron":
                sched_info = f"cron: {j.schedule.expr}{tz_info}"
            elif j.schedule.kind == "every":
                secs = (j.schedule.every_ms or 0) // 1000
                sched_info = f"每 {secs} 秒"
            else:
                sched_info = j.schedule.kind
            lines.append(f"- {mode_icon} {j.name} (id: {j.id}, {sched_info})")
        return "定时任务列表：\n" + "\n".join(lines)
    
    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"✅ 已删除任务 {job_id}"
        return f"❌ 未找到任务 {job_id}"
