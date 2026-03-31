"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig
from nanobot.providers.base import LLMProvider
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool


@dataclass(slots=True)
class WorkerProtocolRecord:
    worker_id: str
    task_id: str
    label: str
    session_key: str
    origin_channel: str
    origin_chat_id: str


@dataclass(slots=True)
class WorkerMailboxEntry:
    task_id: str
    label: str
    task: str
    kind: str
    timestamp: str


@dataclass(slots=True)
class WorkerMailbox:
    worker_id: str
    session_key: str
    origin_channel: str
    origin_chat_id: str
    history: list[WorkerMailboxEntry] = field(default_factory=list)


class SubagentManager:
    """
    Manages background subagent execution.
    
    Subagents are lightweight agent instances that run in the background
    to handle specific tasks. They share the same LLM provider but have
    isolated context and a focused system prompt.
    """
    
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_task_ids: dict[str, set[str]] = defaultdict(set)
        self._worker_records: dict[str, WorkerProtocolRecord] = {}
        self._worker_mailboxes: dict[str, WorkerMailbox] = {}

    def _make_session_key(
        self,
        origin_channel: str,
        origin_chat_id: str,
        session_key: str | None,
    ) -> str:
        return session_key or f"{origin_channel}:{origin_chat_id}"

    def _make_worker_id(self, session_key: str) -> str:
        # Stable per spawned worker. Future follow-up routing can target this ID.
        return f"worker-{uuid.uuid4().hex[:12]}"

    def _make_display_label(self, task: str, label: str | None) -> str:
        return label or task[:30] + ("..." if len(task) > 30 else "")

    def _record_worker_mailbox(
        self,
        *,
        worker_id: str,
        task_id: str,
        label: str,
        task: str,
        session_key: str,
        origin_channel: str,
        origin_chat_id: str,
        kind: str,
    ) -> WorkerMailbox:
        mailbox = self._worker_mailboxes.get(worker_id)
        if mailbox is None:
            mailbox = WorkerMailbox(
                worker_id=worker_id,
                session_key=session_key,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
            )
            self._worker_mailboxes[worker_id] = mailbox
        mailbox.history.append(
            WorkerMailboxEntry(
                task_id=task_id,
                label=label,
                task=task,
                kind=kind,
                timestamp=datetime.now().isoformat(),
            )
        )
        return mailbox

    def _launch_worker_task(
        self,
        record: WorkerProtocolRecord,
        task: str,
        origin: dict[str, str],
        *,
        kind: str,
    ) -> str:
        bg_task = asyncio.create_task(self._run_subagent(record, task, origin))
        self._running_tasks[record.task_id] = bg_task
        self._session_task_ids[record.session_key].add(record.task_id)
        self._worker_records[record.worker_id] = record
        self._record_worker_mailbox(
            worker_id=record.worker_id,
            task_id=record.task_id,
            label=record.label,
            task=task,
            session_key=record.session_key,
            origin_channel=record.origin_channel,
            origin_chat_id=record.origin_chat_id,
            kind=kind,
        )

        # Cleanup when done
        bg_task.add_done_callback(
            lambda _: self._cleanup_task_tracking(
                record.task_id,
                record.session_key,
                record.worker_id,
            )
        )

        logger.info(
            "{} subagent [{}/{}]: {}",
            "Spawned" if kind == "spawn" else "Continued",
            record.task_id,
            record.worker_id,
            record.label,
        )
        action = "started" if kind == "spawn" else "continued"
        return (
            f"Subagent [{record.label}] {action} (id: {record.task_id}, worker: {record.worker_id}). "
            "I'll notify you when it completes."
        )

    def _build_task_notification(
        self,
        record: WorkerProtocolRecord,
        status: str,
        summary: str,
        result: str | None = None,
    ) -> str:
        parts = [
            "<task-notification>",
            f"<task-id>{record.worker_id}</task-id>",
            f"<status>{status}</status>",
            f"<summary>{summary}</summary>",
        ]
        if result is not None:
            parts.append(f"<result>{result}</result>")
        parts.append("</task-notification>")
        return "\n".join(parts)
    
    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """
        Spawn a subagent to execute a task in the background.
        
        Args:
            task: The task description for the subagent.
            label: Optional human-readable label for the task.
            origin_channel: The channel to announce results to.
            origin_chat_id: The chat ID to announce results to.
        
        Returns:
            Status message indicating the subagent was started.
        """
        task_id = str(uuid.uuid4())[:8]
        display_label = self._make_display_label(task, label)
        session_key = self._make_session_key(origin_channel, origin_chat_id, session_key)
        worker_id = self._make_worker_id(session_key)
        
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }
        record = WorkerProtocolRecord(
            worker_id=worker_id,
            task_id=task_id,
            label=display_label,
            session_key=session_key,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
        )
        return self._launch_worker_task(record, task, origin, kind="spawn")

    async def continue_worker(
        self,
        worker_id: str,
        task: str,
        label: str | None = None,
    ) -> str:
        mailbox = self._worker_mailboxes.get(worker_id)
        if mailbox is None:
            return f"Error: worker not found: {worker_id}"
        if worker_id in self._worker_records:
            return f"Error: worker is still running: {worker_id}"

        task_id = str(uuid.uuid4())[:8]
        display_label = self._make_display_label(task, label)
        record = WorkerProtocolRecord(
            worker_id=worker_id,
            task_id=task_id,
            label=display_label,
            session_key=mailbox.session_key,
            origin_channel=mailbox.origin_channel,
            origin_chat_id=mailbox.origin_chat_id,
        )
        origin = {
            "channel": mailbox.origin_channel,
            "chat_id": mailbox.origin_chat_id,
        }
        return self._launch_worker_task(record, task, origin, kind="continue")

    def _cleanup_task_tracking(
        self,
        task_id: str,
        session_key: str,
        worker_id: str | None = None,
    ) -> None:
        """Remove finished task from tracking maps."""
        self._running_tasks.pop(task_id, None)
        session_tasks = self._session_task_ids.get(session_key)
        if not session_tasks:
            if worker_id is not None:
                self._worker_records.pop(worker_id, None)
            return
        session_tasks.discard(task_id)
        if not session_tasks:
            self._session_task_ids.pop(session_key, None)
        if worker_id is not None:
            self._worker_records.pop(worker_id, None)

    def cancel_by_session(self, session_key: str) -> int:
        """
        Cancel running subagent tasks for a session.

        Returns:
            Number of running tasks that were cancelled.
        """
        task_ids = list(self._session_task_ids.get(session_key, set()))
        cancelled = 0

        for task_id in task_ids:
            task = self._running_tasks.get(task_id)
            if task is None or task.done():
                self._cleanup_task_tracking(task_id, session_key)
                continue
            task.cancel()
            cancelled += 1

        return cancelled
    
    async def _run_subagent(
        self,
        record: WorkerProtocolRecord,
        task: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(
            "Subagent [{}/{}] starting task: {}",
            record.task_id,
            record.worker_id,
            record.label,
        )
        
        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(allowed_dir=allowed_dir))
            tools.register(WriteFileTool(allowed_dir=allowed_dir))
            tools.register(EditFileTool(allowed_dir=allowed_dir))
            tools.register(ListDirTool(allowed_dir=allowed_dir))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
            ))
            tools.register(WebSearchTool(api_key=self.brave_api_key))
            tools.register(WebFetchTool())
            
            # Build messages with subagent-specific prompt
            system_prompt = self._build_subagent_prompt(task)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]
            
            # Run agent loop (limited iterations)
            max_iterations = 15
            iteration = 0
            final_result: str | None = None
            
            while iteration < max_iterations:
                iteration += 1
                
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    tool_choice="auto",
                    model=self.model,
                )
                
                if response.has_tool_calls:
                    # Add assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })
                    
                    # Execute tools
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments)
                        logger.debug(
                            "Subagent [{}/{}] executing: {} with arguments: {}",
                            record.task_id,
                            record.worker_id,
                            tool_call.name,
                            args_str,
                        )
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    final_result = response.content
                    break
            
            if not final_result:
                final_result = "（子代理已完成执行，但未返回文本结果。请检查工具执行日志。）"
            
            logger.info(
                "Subagent [{}/{}] completed successfully",
                record.task_id,
                record.worker_id,
            )
            await self._announce_result(record, task, final_result, origin, "ok")
            
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(
                "Subagent [{}/{}] failed: {}",
                record.task_id,
                record.worker_id,
                e,
            )
            await self._announce_result(record, task, error_msg, origin, "error")
    
    async def _announce_result(
        self,
        record: WorkerProtocolRecord,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"
        notification_status = "completed" if status == "ok" else "failed"
        notification = self._build_task_notification(
            record,
            notification_status,
            f"Agent '{record.label}' {status_text}",
            result,
        )
        
        announce_content = f"""[Subagent '{record.label}' {status_text}]

Worker ID: {record.worker_id}

Task: {task}

Result:
{result}

Protocol:
{notification}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""
        
        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )
        
        await self.bus.publish_inbound(msg)
        logger.debug(
            "Subagent [{}/{}] announced result to {}:{}",
            record.task_id,
            record.worker_id,
            origin["channel"],
            origin["chat_id"],
        )
    
    def _build_subagent_prompt(self, task: str) -> str:
        """Build a focused system prompt for the subagent."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"""# Subagent

You are a subagent spawned by the main agent to complete a specific task.
Current time: {now}

## Your Task
{task}

## Rules
1. Stay focused - complete only the assigned task, nothing else
2. Your final response will be reported back to the main agent
3. Do not initiate conversations or take on side tasks
4. Be concise but informative in your findings

## What You Can Do
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages
- Complete the task thoroughly

## What You Cannot Do
- Send messages directly to users (no message tool available)
- Spawn other subagents
- Access the main agent's conversation history

## Workspace
Your workspace is at: {self.workspace}

When you have completed the task, provide a clear summary of your findings or actions."""
    
    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
