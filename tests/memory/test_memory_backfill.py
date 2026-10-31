"""Resumable memory re-indexing after the embedding index changes."""

from __future__ import annotations

import asyncio
import json

import pytest
from memory_helpers import MemoryHarness, TopicEmbedder

from yoreni.config.schema import Config, MemoryConfig, ProvidersConfig
from yoreni.memory.backfill import PAUSED, RUNNING, STATE_KEY
from yoreni.memory.embeddings import embedding_index_key
from yoreni.memory.service import MemoryService


def _commit(harness: MemoryHarness, run_id: str, **draft_kwargs) -> str:
    harness.record(run_id, ("user", draft_kwargs.pop("evidence_text", "remember this")))
    source = harness.evidence_id(run_id)
    return harness.service.store.commit_candidates([
        harness.draft(sources=(source,), **draft_kwargs),
    ])[0]


async def test_a_gap_becomes_one_task_that_finishes(tmp_path):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    harness = MemoryHarness(service, tmp_path)
    _commit(harness, "run-1")

    assert service.backfill.snapshot() == {"active": False}
    assert service.backfill.reconcile() == {
        "active": True,
        "status": RUNNING,
        "model": "test-embedding",
        "done": 0,
        "total": 1,
        "remaining": 1,
        "eta_ms": None,
    }

    assert await service.backfill.step() == 1
    # The task disappears once the gap is closed; nothing is left running.
    assert service.backfill.snapshot() == {"active": False}
    assert service.store.count_missing_vectors(embedding_index_key(service.embedder.settings)) == 0


async def test_a_stray_gap_is_picked_up_without_an_existing_task(tmp_path):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    _commit(MemoryHarness(service, tmp_path), "run-1")
    assert service.backfill.snapshot() == {"active": False}

    assert await service.backfill.step() == 1
    assert service.store.count_missing_vectors(embedding_index_key(service.embedder.settings)) == 0


async def test_pausing_stops_batches_and_survives_a_restart(tmp_path):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    harness = MemoryHarness(service, tmp_path)
    _commit(harness, "run-1")
    service.backfill.reconcile()

    assert (await service.backfill.pause())["status"] == PAUSED
    assert await service.backfill.step() == 0
    assert service.store.count_missing_vectors(embedding_index_key(service.embedder.settings)) == 1

    restarted = MemoryService(
        tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}),
    )
    snapshot = restarted.backfill.reconcile()
    assert snapshot["status"] == PAUSED
    assert await restarted.backfill.step() == 0

    assert (await restarted.backfill.resume())["status"] == RUNNING
    assert await restarted.backfill.step() == 1


async def test_a_new_index_discards_the_unfinished_task(tmp_path):
    first = TopicEmbedder({"python": [1.0, 0.0]})
    service = MemoryService(tmp_path, MemoryConfig(), embedder=first)
    harness = MemoryHarness(service, tmp_path)
    _commit(harness, "run-1")
    _commit(harness, "run-2", subject="user", predicate="editor", text="The user edits in Vim.")
    service.backfill.reconcile()
    # Simulate progress that never reached the store: the task must not be
    # carried over to an index it does not describe.
    service.store.meta_set(STATE_KEY, json.dumps({
        "index_key": embedding_index_key(first.settings),
        "model": "test-embedding",
        "status": RUNNING,
        "done": 1,
        "started_at": "2026-01-01T00:00:00Z",
        "active_ms": 1000,
        "updated_at": "2026-01-01T00:00:00Z",
    }))

    service.reload_config(
        Config.model_validate({
            "memory": {
                "embedding": {
                    "model": "other-embed",
                    "api_base": "http://127.0.0.1:11434/v1",
                },
            },
        }).memory,
        providers=ProvidersConfig(),
    )
    snapshot = service.backfill.snapshot()

    assert snapshot["status"] == RUNNING
    assert snapshot["done"] == 0
    assert snapshot["total"] == 2
    stored = json.loads(service.store.meta_get(STATE_KEY))
    assert stored["index_key"] == embedding_index_key(service.embedder.settings)
    assert stored["model"] == "other-embed"
    assert stored["done"] == 0


async def test_progress_survives_a_restart_and_keeps_its_estimate(tmp_path):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    harness = MemoryHarness(service, tmp_path)
    first = _commit(harness, "run-1")
    _commit(harness, "run-2", subject="user", predicate="editor", text="The user edits in Vim.")
    service.backfill.reconcile()
    key = embedding_index_key(service.embedder.settings)
    service.store.upsert_vector(first, key, [1.0, 0.0])

    state = json.loads(service.store.meta_get(STATE_KEY))
    state["done"] = 1
    state["active_ms"] = 2000
    service.store.meta_set(STATE_KEY, json.dumps(state))

    restarted = MemoryService(
        tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}),
    )
    snapshot = restarted.backfill.reconcile()
    assert snapshot["done"] == 1
    assert snapshot["remaining"] == 1
    assert snapshot["total"] == 2
    assert snapshot["eta_ms"] == 2000


async def test_vectors_are_not_filed_under_a_replaced_index(tmp_path):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    harness = MemoryHarness(service, tmp_path)
    _commit(harness, "run-1")
    stale = service.embedder
    stale_key = embedding_index_key(stale.settings)

    service.embedder = TopicEmbedder({"python": [1.0, 0.0, 0.0]})
    facts = service.store.missing_vectors(stale_key, 10)

    assert await service._embed(facts, embedder=stale, index_key=stale_key) == 0
    assert service.store.count_missing_vectors(stale_key) == 1


async def test_the_background_loop_finishes_the_task(tmp_path):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    _commit(MemoryHarness(service, tmp_path), "run-1")

    await service.backfill.start()
    try:
        for _ in range(100):
            if not service.backfill.snapshot()["active"]:
                break
            await asyncio.sleep(0.05)
        assert service.backfill.snapshot() == {"active": False}
        assert service.store.count_missing_vectors(
            embedding_index_key(service.embedder.settings)
        ) == 0
    finally:
        await service.backfill.stop()


@pytest.mark.parametrize("missing_before", [True, False])
def test_reconcile_is_idempotent(tmp_path, missing_before):
    service = MemoryService(tmp_path, MemoryConfig(), embedder=TopicEmbedder({"python": [1.0, 0.0]}))
    if missing_before:
        MemoryHarness(service, tmp_path).commit("run-1")
    service.backfill.reconcile()
    first = service.backfill.snapshot()
    assert service.backfill.reconcile() == first
