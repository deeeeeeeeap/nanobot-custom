"""Coordinator helpers for fan-out, notification collection, and aggregation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

from nanobot.utils.helpers import truncate_string

NotificationStatus = Literal["completed", "failed", "killed"]
RequestMode = Literal["spawn", "continue"]

_TASK_NOTIFICATION_RE = re.compile(
    r"<task-notification>(.*?)</task-notification>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(slots=True)
class CoordinatorTaskSpec:
    task: str
    label: str | None = None
    worker_id: str | None = None


@dataclass(slots=True)
class CoordinatorWorkerRequest:
    mode: RequestMode
    task: str
    label: str
    worker_id: str | None = None
    origin_channel: str | None = None
    origin_chat_id: str | None = None
    session_key: str | None = None


@dataclass(slots=True)
class CoordinatorWorkerRecord:
    worker_id: str
    task: str
    label: str
    origin_channel: str | None = None
    origin_chat_id: str | None = None
    session_key: str | None = None


@dataclass(slots=True)
class CoordinatorWorkerUsage:
    total_tokens: int | None = None
    tool_uses: int | None = None
    duration_ms: int | None = None


@dataclass(slots=True)
class CoordinatorWorkerNotification:
    worker_id: str
    status: str
    summary: str
    result: str | None = None
    usage: CoordinatorWorkerUsage | None = None


@dataclass(slots=True)
class CoordinatorAggregateItem:
    worker_id: str
    task_label: str
    status: str
    summary: str
    result: str | None = None
    usage: CoordinatorWorkerUsage | None = None


@dataclass(slots=True)
class CoordinatorAggregateSummary:
    text: str
    items: list[CoordinatorAggregateItem] = field(default_factory=list)
    worker_labels: dict[str, str] = field(default_factory=dict)


def _make_label(task: str, label: str | None) -> str:
    if label and label.strip():
        return label.strip()
    return truncate_string(task.strip(), 30)


def _extract_tag(block: str, tag: str, *, required: bool = True) -> str | None:
    match = re.search(
        rf"<{tag}>(.*?)</{tag}>",
        block,
        re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        if required:
            raise ValueError(f"missing <{tag}> in task notification")
        return None
    return match.group(1).strip()


def _parse_usage(block: str) -> CoordinatorWorkerUsage | None:
    usage_match = re.search(
        r"<usage>(.*?)</usage>",
        block,
        re.DOTALL | re.IGNORECASE,
    )
    if usage_match is None:
        return None

    usage_block = usage_match.group(1)

    def _parse_int(tag: str) -> int | None:
        value = _extract_tag(usage_block, tag, required=False)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"invalid integer in <{tag}>: {value!r}") from exc

    return CoordinatorWorkerUsage(
        total_tokens=_parse_int("total_tokens"),
        tool_uses=_parse_int("tool_uses"),
        duration_ms=_parse_int("duration_ms"),
    )


def parse_task_notifications(text: str) -> list[CoordinatorWorkerNotification]:
    notifications: list[CoordinatorWorkerNotification] = []
    for match in _TASK_NOTIFICATION_RE.finditer(text):
        block = match.group(1)
        notifications.append(
            CoordinatorWorkerNotification(
                worker_id=_extract_tag(block, "task-id"),
                status=_extract_tag(block, "status"),
                summary=_extract_tag(block, "summary"),
                result=_extract_tag(block, "result", required=False),
                usage=_parse_usage(block),
            ),
        )
    return notifications


def parse_task_notification(text: str) -> CoordinatorWorkerNotification | None:
    notifications = parse_task_notifications(text)
    if not notifications:
        return None
    return notifications[0]


class CoordinatorHelper:
    """Collects worker requests and notifications for coordinator mode."""

    def __init__(self) -> None:
        self._worker_records: dict[str, CoordinatorWorkerRecord] = {}
        self._notifications: list[CoordinatorWorkerNotification] = []

    def register_worker(
        self,
        worker_id: str,
        task: str,
        label: str | None = None,
        *,
        origin_channel: str | None = None,
        origin_chat_id: str | None = None,
        session_key: str | None = None,
    ) -> CoordinatorWorkerRecord:
        record = CoordinatorWorkerRecord(
            worker_id=worker_id,
            task=task,
            label=_make_label(task, label),
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
        )
        self._worker_records[worker_id] = record
        return record

    def build_fan_out(
        self,
        tasks: Sequence[CoordinatorTaskSpec],
        *,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> list[CoordinatorWorkerRequest]:
        requests: list[CoordinatorWorkerRequest] = []
        for task_spec in tasks:
            record = (
                self._worker_records.get(task_spec.worker_id)
                if task_spec.worker_id
                else None
            )
            label = _make_label(task_spec.task, task_spec.label or record.label if record else task_spec.label)
            if task_spec.worker_id:
                requests.append(
                    CoordinatorWorkerRequest(
                        mode="continue",
                        task=task_spec.task,
                        label=label,
                        worker_id=task_spec.worker_id,
                        origin_channel=record.origin_channel if record else origin_channel,
                        origin_chat_id=record.origin_chat_id if record else origin_chat_id,
                        session_key=record.session_key if record else session_key,
                    ),
                )
            else:
                requests.append(
                    CoordinatorWorkerRequest(
                        mode="spawn",
                        task=task_spec.task,
                        label=label,
                        origin_channel=origin_channel,
                        origin_chat_id=origin_chat_id,
                        session_key=session_key,
                    ),
                )
        return requests

    def route_follow_up(
        self,
        worker_id: str,
        task: str,
        label: str | None = None,
    ) -> CoordinatorWorkerRequest:
        record = self._worker_records.get(worker_id)
        resolved_label = _make_label(
            task,
            label if label is not None else (record.label if record else None),
        )
        return CoordinatorWorkerRequest(
            mode="continue",
            task=task,
            label=resolved_label,
            worker_id=worker_id,
            origin_channel=record.origin_channel if record else None,
            origin_chat_id=record.origin_chat_id if record else None,
            session_key=record.session_key if record else None,
        )

    def collect_notifications(
        self,
        payloads: Iterable[str | CoordinatorWorkerNotification],
    ) -> list[CoordinatorWorkerNotification]:
        collected: list[CoordinatorWorkerNotification] = []
        for payload in payloads:
            if isinstance(payload, CoordinatorWorkerNotification):
                notification = payload
            else:
                parsed = parse_task_notifications(payload)
                collected.extend(parsed)
                continue
            collected.append(notification)
        self._notifications.extend(collected)
        return collected

    def aggregate_notifications(
        self,
        notifications: Iterable[CoordinatorWorkerNotification | str] | None = None,
    ) -> CoordinatorAggregateSummary:
        resolved_notifications: list[CoordinatorWorkerNotification] = []
        if notifications is None:
            resolved_notifications = list(self._notifications)
        else:
            for item in notifications:
                if isinstance(item, CoordinatorWorkerNotification):
                    resolved_notifications.append(item)
                else:
                    resolved_notifications.extend(parse_task_notifications(item))

        items: list[CoordinatorAggregateItem] = []
        worker_labels: dict[str, str] = {}
        text_parts: list[str] = []

        for notification in resolved_notifications:
            record = self._worker_records.get(notification.worker_id)
            task_label = record.label if record is not None else notification.worker_id
            worker_labels[notification.worker_id] = task_label

            item = CoordinatorAggregateItem(
                worker_id=notification.worker_id,
                task_label=task_label,
                status=notification.status,
                summary=notification.summary,
                result=notification.result,
                usage=notification.usage,
            )
            items.append(item)
            preview = truncate_string(
                notification.summary or notification.result or "No summary",
                120,
            )
            text_parts.append(
                f"{notification.worker_id} [{task_label}] {notification.status}: {preview}",
            )

        text = " | ".join(text_parts) if text_parts else "No worker results yet."
        return CoordinatorAggregateSummary(
            text=text,
            items=items,
            worker_labels=worker_labels,
        )


__all__ = [
    "CoordinatorAggregateItem",
    "CoordinatorAggregateSummary",
    "CoordinatorHelper",
    "CoordinatorTaskSpec",
    "CoordinatorWorkerNotification",
    "CoordinatorWorkerRecord",
    "CoordinatorWorkerRequest",
    "CoordinatorWorkerUsage",
    "parse_task_notification",
    "parse_task_notifications",
]
