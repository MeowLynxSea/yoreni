"""Contracts for the Dream memory-consolidation tool."""

from __future__ import annotations

import asyncio

from yoreni.agent.memory import MemoryStore
from yoreni.agent.tools.dream import DreamTool


class FakeCron:
    def __init__(self, job: object | None) -> None:
        self.job = job
        self.runs: list[tuple[str, bool]] = []

    def get_job(self, job_id: str) -> object | None:
        return self.job if job_id == "dream" else None

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        self.runs.append((job_id, force))
        return True


async def test_run_starts_a_background_consolidation(tmp_path):
    cron = FakeCron(job=object())
    tool = DreamTool(MemoryStore(tmp_path), cron)

    result = await tool.execute(action="run")
    await asyncio.sleep(0)

    assert "started" in result
    assert cron.runs == [("dream", True)]


async def test_run_reports_when_consolidation_is_off(tmp_path):
    tool = DreamTool(MemoryStore(tmp_path), None)

    result = await tool.execute(action="run")

    assert "turned off" in result


async def test_versions_lists_consolidations_and_restore_undoes_one(tmp_path):
    memory = MemoryStore(tmp_path)
    memory.git.init()
    memory.write_memory("baseline")
    memory.git.auto_commit("setup: baseline memory")
    memory.write_memory("consolidated")
    memory.git.auto_commit("dream: periodic memory consolidation")
    tool = DreamTool(memory, None)

    listing = await tool.execute(action="versions")
    commit = listing.splitlines()[1].split()[0]
    restored = await tool.execute(action="restore", commit=commit)

    assert "dream: periodic memory consolidation" in listing
    assert "restored" in restored
    assert memory.read_memory().strip() == "baseline"


async def test_restore_refuses_a_commit_that_is_not_a_consolidation(tmp_path):
    memory = MemoryStore(tmp_path)
    memory.git.init()
    memory.write_memory("baseline")
    memory.git.auto_commit("setup: baseline memory")
    setup_commit = memory.git.log(max_entries=1)[0].sha
    tool = DreamTool(memory, None)

    result = await tool.execute(action="restore", commit=setup_commit)

    assert "not a memory consolidation" in result
    assert memory.read_memory().strip() == "baseline"
