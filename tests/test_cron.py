from pathlib import Path

import pytest

from nanobot.agent.tools.cron import CronTool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule


def test_cron_service_rejects_invalid_cron_expr(tmp_path: Path) -> None:
    service = CronService(tmp_path / "jobs.json")
    with pytest.raises(ValueError):
        service.add_job(
            name="bad",
            schedule=CronSchedule(kind="cron", expr="not-a-cron"),
            message="hello",
        )


def test_cron_service_rejects_invalid_timezone(tmp_path: Path) -> None:
    service = CronService(tmp_path / "jobs.json")
    with pytest.raises(ValueError):
        service.add_job(
            name="bad",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="Mars/Base"),
            message="hello",
        )


def test_cron_service_adds_every_job(tmp_path: Path) -> None:
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        name="ok",
        schedule=CronSchedule(kind="every", every_ms=30_000),
        message="ping",
    )
    assert job.id
    assert job.state.next_run_at_ms is not None


def test_cron_service_persists_jobs_across_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "jobs.json"
    service = CronService(store_path)
    job = service.add_job(
        name="heartbeat",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="ping",
        deliver=True,
        channel="telegram",
        to="123",
    )
    service.enable_job(job.id, enabled=False)

    restarted = CronService(store_path)
    jobs = restarted.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    loaded = jobs[0]
    assert loaded.id == job.id
    assert loaded.enabled is False
    assert loaded.payload.message == "ping"
    assert loaded.payload.channel == "telegram"
    assert loaded.payload.to == "123"
    assert loaded.state.next_run_at_ms is None

    enabled = restarted.enable_job(job.id, enabled=True)
    assert enabled is not None
    assert enabled.state.next_run_at_ms is not None

    restarted_again = CronService(store_path)
    loaded_again = restarted_again.list_jobs(include_disabled=True)[0]
    assert loaded_again.enabled is True
    assert loaded_again.state.next_run_at_ms is not None


async def test_cron_tool_reports_invalid_at_datetime(tmp_path: Path) -> None:
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    tool.set_context("telegram", "123")
    result = await tool.execute(action="add", message="hello", at="bad-date")
    assert "invalid 'at'" in result
