"""Agent-facing controls for existing scheduled jobs."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from yoreni.agent.tools.cron import CronTool
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.cron.service import CronService
from yoreni.cron.types import CronJob, CronPayload, CronSchedule


def _registry(service: CronService) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CronTool(service))
    return registry


async def test_pause_resume_preserves_job_and_history(tmp_path: Path) -> None:
    store_path = tmp_path / "jobs.json"
    async def complete(_job: CronJob) -> str:
        return "Report completed"

    service = CronService(store_path, on_job=complete)
    job = service.add_job(
        name="Morning report",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Summarize open pull requests",
        agent_id="agent-1",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    await service.run_job(job.id, force=True)
    job = service.get_job(job.id)
    assert job is not None and job.state.run_history
    original = (job.id, asdict(job.schedule), asdict(job.payload), list(job.state.run_history))
    registry = _registry(service)

    await registry.execute("cron", {"action": "disable", "job_id": job.id})
    paused = CronService(store_path).get_job(job.id)
    assert paused is not None and not paused.enabled
    assert paused.state.next_run_at_ms is None
    assert job.name not in await registry.execute("cron", {"action": "list"})
    listing = await registry.execute("cron", {"action": "list", "include_disabled": True})
    assert job.name in listing and "disabled" in listing

    await registry.execute("cron", {"action": "enable", "job_id": job.id})
    resumed = CronService(store_path).get_job(job.id)
    assert resumed is not None and resumed.enabled
    assert resumed.state.next_run_at_ms is not None
    assert (resumed.id, asdict(resumed.schedule), asdict(resumed.payload), resumed.state.run_history) == original


@pytest.mark.parametrize("action", ["enable", "disable"])
async def test_tool_cannot_toggle_system_job(tmp_path: Path, action: str) -> None:
    service = CronService(tmp_path / "jobs.json")
    job = service.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        payload=CronPayload(kind="system_event", message="dream"),
    ))
    result = await _registry(service).execute("cron", {"action": action, "job_id": job.id})
    assert "protected" in result
    assert job.enabled


async def test_tool_cannot_enable_job_without_output_destination(tmp_path: Path) -> None:
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        name="Legacy job", schedule=CronSchedule(kind="every", every_ms=60_000), message="report",
    )
    result = await _registry(service).execute("cron", {"action": "enable", "job_id": job.id})
    assert "no output destination" in result
    assert not job.enabled


@pytest.mark.parametrize("action", ["enable", "disable"])
async def test_toggle_requires_job_id(tmp_path: Path, action: str) -> None:
    registry = _registry(CronService(tmp_path / "jobs.json"))
    result = await registry.execute("cron", {"action": action})
    assert "job_id" in result and "required" in result


@pytest.mark.parametrize("action", ["enable", "disable"])
async def test_toggle_reports_missing_job(tmp_path: Path, action: str) -> None:
    registry = _registry(CronService(tmp_path / "jobs.json"))
    result = await registry.execute("cron", {"action": action, "job_id": "missing"})
    assert "not found" in result
