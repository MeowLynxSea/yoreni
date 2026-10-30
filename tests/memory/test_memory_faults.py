"""Crash and concurrency recovery across SQLite, Git and the job queue."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from memory_helpers import ScriptedProvider, TopicEmbedder

from yoreni.memory.core import USER_PATH
from yoreni.memory.dream import DreamConsolidator
from yoreni.memory.models import FactStatus
from yoreni.providers.base import LLMResponse
from yoreni.utils.llm_runtime import LLMRuntime


def _runtime(*responses: LLMResponse) -> LLMRuntime:
    return LLMRuntime.capture(
        ScriptedProvider(*responses), "test-model", context_window_tokens=128_000,
    )


def _update_core() -> LLMResponse:
    import json

    return LLMResponse(content=json.dumps({"operations": [{
        "op": "update_core", "file": USER_PATH, "content": "- consolidated\n",
    }]}))


async def test_simultaneous_run_completions_create_distinct_evidence_and_jobs(harness):
    errors: list[BaseException] = []

    def complete(index: int) -> None:
        try:
            harness.record(f"run-{index}", ("user", f"fact number {index}"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=complete, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert harness.service.store.job_counts() == (6, 0)
    for index in range(6):
        assert len(harness.service.store.evidence_for_run(f"run-{index}")) == 1


def test_repeated_run_completion_is_idempotent(harness):
    harness.record("run-1", ("user", "hello"))
    harness.record("run-1", ("user", "hello"))

    assert len(harness.service.store.evidence_for_run("run-1")) == 1
    assert harness.service.store.job_counts() == (1, 0)


async def test_a_crashed_extraction_job_is_retried_after_the_lease(harness):
    harness.record("run-1", ("user", "I prefer Chinese."))
    evidence_id = harness.evidence_id("run-1")
    [job] = harness.service.store.claim_jobs("extract", 1)
    assert job["attempts"] == 1

    harness.service.store.reset_stale_jobs(older_than_seconds=0)
    provider = ScriptedProvider(LLMResponse(content=json.dumps({"facts": [{
        "subject": "user",
        "predicate": "preferred_language",
        "text": "The user prefers Chinese.",
        "kind": "user_preference",
        "sources": [evidence_id],
    }]})))

    assert await harness.service.extract_pending(
        LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000), limit=1,
    ) == 1
    assert harness.service.store.job_counts() == (0, 0)
    assert harness.service.stats()["active_facts"] == 1


async def test_vector_write_failure_keeps_the_fact_and_allows_backfill(harness, monkeypatch):
    harness.record("run-1", ("user", "My name is Ada."))
    harness.service.embedder = TopicEmbedder({"ada": [1.0, 0.0]})
    store = harness.service.store
    original = store.upsert_vector

    def fail(*args, **kwargs):
        raise RuntimeError("disk full while writing the vector")

    monkeypatch.setattr(store, "upsert_vector", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        await harness.service.remember(
            run_id="run-1", subject="user", predicate="name",
            text="The user's name is Ada.", kind="user_fact",
        )

    assert harness.service.stats()["active_facts"] == 1
    fact = store.recent_by_status(FactStatus.ACTIVE, 1)[0]
    assert store.missing_vectors("test-embedding:2", 10) == [fact]

    monkeypatch.setattr(store, "upsert_vector", original)
    assert await harness.service.embed_missing() == 1
    assert store.missing_vectors("test-embedding:2", 10) == []


async def test_git_success_then_status_failure_does_not_duplicate_the_version(
    harness, monkeypatch,
):
    harness.service.git.init()
    harness.commit("run-1", text="The user prefers Chinese.")
    store = harness.service.store
    original = store.update_dream_run
    failed = False

    def flaky(run_id, **kwargs):
        nonlocal failed
        if kwargs.get("status") == "git_committed" and not failed:
            failed = True
            raise RuntimeError("status write failed after the commit")
        return original(run_id, **kwargs)

    monkeypatch.setattr(store, "update_dream_run", flaky)
    with pytest.raises(RuntimeError, match="status write failed"):
        await DreamConsolidator(harness.service).consolidate(_runtime(_update_core()))

    commits = harness.service.git.log()
    assert len(commits) == 2  # init + the consolidation
    run_id = store.unfinished_dream_runs()[0]["id"]
    assert store.dream_run(run_id)["status"] == "files_written"

    monkeypatch.setattr(store, "update_dream_run", original)
    result = await DreamConsolidator(harness.service).consolidate(_runtime())

    assert result.status == "idle"
    assert store.dream_run(run_id)["status"] == "completed"
    assert len(harness.service.git.log()) == len(commits)
    assert store.meta_get("dream_fact_cursor") == str(store.max_fact_rowid())


async def test_startup_recovery_applies_a_stored_proposal(harness):
    harness.record("run-1", ("user", "I moved to Berlin."))
    evidence_id = harness.evidence_id("run-1")
    harness.commit("run-2", subject="user", predicate="editor", text="The user uses Neovim.")
    store = harness.service.store
    run_id = store.claim_dream_run(cursor_from=0, cursor_to=store.max_fact_rowid())
    assert run_id is not None
    store.update_dream_run(run_id, proposal={"operations": [{
        "op": "add_fact", "subject": "user", "predicate": "city",
        "text": "The user lives in Berlin.", "kind": "user_fact",
        "scope": "global", "evidence": [evidence_id],
    }]})

    assert DreamConsolidator(harness.service).recover() == 1

    record = store.dream_run(run_id)
    assert record["status"] == "completed"
    assert any(fact.predicate == "city" for fact in store.recent_by_status(FactStatus.ACTIVE, 10))


async def test_startup_recovery_fails_a_planned_run_without_a_proposal(harness):
    store = harness.service.store
    run_id = store.claim_dream_run(cursor_from=0, cursor_to=0)
    assert run_id is not None

    assert DreamConsolidator(harness.service).recover() == 1

    record = store.dream_run(run_id)
    assert record["status"] == "failed"
    assert "before a proposal was stored" in record["error"]


async def test_a_run_completed_during_dreaming_keeps_its_evidence_and_job(harness):
    harness.commit("run-1", text="The user prefers Chinese.")

    class BlockingProvider(ScriptedProvider):
        def __init__(self, *responses):
            super().__init__(*responses)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def chat(self, messages, **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().chat(messages, **kwargs)

    from yoreni.memory.dream import DreamConsolidator

    provider = BlockingProvider(LLMResponse(content='{"operations": []}'))
    task = asyncio.create_task(
        DreamConsolidator(harness.service).consolidate(
            LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)
        )
    )
    await provider.entered.wait()
    harness.record("run-during-dream", ("user", "I also like tea."))

    assert harness.service.store.evidence_for_run("run-during-dream")
    provider.release.set()
    result = await task

    assert result.status == "completed"
    assert harness.service.store.evidence_for_run("run-during-dream")
    assert harness.service.store.job_counts() == (2, 0)
