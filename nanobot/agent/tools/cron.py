"""Cron tool for scheduling reminders and tasks."""

import re
from datetime import datetime
from typing import Any

from loguru import logger

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
        """Set current channel/chat context for delivery."""
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule reminders or agent tasks. "
            "mode='remind' sends static message text; "
            "mode='agent' makes the agent execute the prompt and deliver the result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform.",
                },
                "message": {
                    "type": "string",
                    "description": "Reminder text or agent prompt for add action.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["remind", "agent"],
                    "description": "Task mode: static reminder or full agent run.",
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds for recurring tasks.",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *'.",
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone for cron_expr, e.g. 'Asia/Shanghai'.",
                },
                "at": {
                    "type": "string",
                    "description": "One-shot ISO datetime, e.g. '2026-02-14T10:30:00'.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Job ID for remove action.",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        message: str = "",
        mode: str = "remind",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        timezone: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return self._add_job(message, mode, every_seconds, cron_expr, timezone, at)
        if action == "list":
            return self._list_jobs()
        if action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _add_job(
        self,
        message: str,
        mode: str,
        every_seconds: int | None,
        cron_expr: str | None,
        timezone: str | None,
        at: str | None = None,
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"

        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=timezone)
        elif at:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return "Error: invalid 'at' datetime format, expected ISO-8601"
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        payload_kind = "agent_turn" if mode == "agent" else "system_event"
        mode_label = "Agent" if mode == "agent" else "Remind"

        if mode == "agent":
            tool_call_match = re.match(
                r"^(exec|cron|weather|web_search|web_fetch|message)\s*\(",
                message.strip(),
            )
            if tool_call_match:
                cmd_match = re.search(r"command=['\"](.+?)['\"]", message)
                if cmd_match:
                    message = f"Execute command {cmd_match.group(1)} and report the result."
                else:
                    message = f"Please complete this task and report the result: {message}"
                logger.warning(f"Normalized cron agent message: {message[:120]}")

        try:
            job = self._cron.add_job(
                name=message[:30],
                schedule=schedule,
                message=message,
                deliver=True,
                channel=self._channel,
                to=self._chat_id,
                payload_kind=payload_kind,
                delete_after_run=delete_after,
            )
        except ValueError as e:
            return f"Error: {e}"

        at_info = " (one-shot)" if delete_after else ""
        return (
            f"Created cron job [{mode_label}]{at_info}\n"
            f"Name: {job.name}\n"
            f"ID: {job.id}"
        )

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No cron jobs found."

        lines = []
        for job in jobs:
            mode_icon = "A" if job.payload.kind == "agent_turn" else "R"
            tz_info = f" ({job.schedule.tz})" if job.schedule.tz else ""
            if job.schedule.kind == "cron":
                sched_info = f"cron: {job.schedule.expr}{tz_info}"
            elif job.schedule.kind == "every":
                sched_info = f"every {(job.schedule.every_ms or 0) // 1000}s"
            else:
                sched_info = "one-shot"
            lines.append(f"- [{mode_icon}] {job.name} (id: {job.id}, {sched_info})")
        return "Cron jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job not found: {job_id}"
