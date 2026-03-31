from nanobot.agent.coordinator import (
    CoordinatorHelper,
    CoordinatorTaskSpec,
    CoordinatorWorkerNotification,
    CoordinatorWorkerUsage,
    parse_task_notification,
    parse_task_notifications,
)


def test_parse_task_notification_extracts_fields() -> None:
    payload = """
<task-notification>
<task-id>worker-123</task-id>
<status>completed</status>
<summary>done</summary>
<result>ok</result>
<usage>
<total_tokens>101</total_tokens>
<tool_uses>2</tool_uses>
<duration_ms>300</duration_ms>
</usage>
</task-notification>
"""

    notification = parse_task_notification(payload)
    assert notification is not None
    assert notification.worker_id == "worker-123"
    assert notification.status == "completed"
    assert notification.summary == "done"
    assert notification.result == "ok"
    assert notification.usage is not None
    assert notification.usage.total_tokens == 101
    assert notification.usage.tool_uses == 2
    assert notification.usage.duration_ms == 300


def test_parse_task_notifications_collects_multiple_blocks() -> None:
    payload = """
<task-notification><task-id>a</task-id><status>completed</status><summary>one</summary></task-notification>
<task-notification><task-id>b</task-id><status>failed</status><summary>two</summary><result>boom</result></task-notification>
"""
    notifications = parse_task_notifications(payload)
    assert [item.worker_id for item in notifications] == ["a", "b"]
    assert notifications[1].status == "failed"
    assert notifications[1].result == "boom"


def test_coordinator_build_fan_out_and_follow_up() -> None:
    helper = CoordinatorHelper()
    helper.register_worker(
        "worker-123",
        "initial task",
        "Initial Task",
        origin_channel="cli",
        origin_chat_id="chat-a",
        session_key="cli:chat-a",
    )

    requests = helper.build_fan_out(
        [
            CoordinatorTaskSpec(task="new task", label="New Task"),
            CoordinatorTaskSpec(task="follow-up task", worker_id="worker-123"),
        ],
        origin_channel="cli",
        origin_chat_id="chat-a",
        session_key="cli:chat-a",
    )

    assert requests[0].mode == "spawn"
    assert requests[0].label == "New Task"
    assert requests[1].mode == "continue"
    assert requests[1].worker_id == "worker-123"
    assert requests[1].origin_channel == "cli"
    assert requests[1].origin_chat_id == "chat-a"
    assert requests[1].session_key == "cli:chat-a"

    follow_up = helper.route_follow_up("worker-123", "extra analysis")
    assert follow_up.mode == "continue"
    assert follow_up.worker_id == "worker-123"
    assert follow_up.label == "Initial Task"


def test_coordinator_aggregate_preserves_worker_mapping() -> None:
    helper = CoordinatorHelper()
    helper.register_worker("worker-a", "task a", "Alpha")
    helper.register_worker("worker-b", "task b", "Beta")

    summary = helper.aggregate_notifications(
        [
            CoordinatorWorkerNotification(
                worker_id="worker-a",
                status="completed",
                summary="alpha summary",
                result="alpha result",
                usage=CoordinatorWorkerUsage(total_tokens=20),
            ),
            CoordinatorWorkerNotification(
                worker_id="worker-b",
                status="failed",
                summary="beta summary",
                result="beta result",
            ),
        ]
    )

    assert summary.worker_labels == {"worker-a": "Alpha", "worker-b": "Beta"}
    assert len(summary.items) == 2
    assert summary.items[0].task_label == "Alpha"
    assert summary.items[1].task_label == "Beta"
    assert "worker-a [Alpha] completed: alpha summary" in summary.text
    assert "worker-b [Beta] failed: beta summary" in summary.text
