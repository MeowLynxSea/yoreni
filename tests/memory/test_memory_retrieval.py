"""Hybrid retrieval: keyword, semantic, scope, time validity and provenance."""

from __future__ import annotations

import pytest
from memory_helpers import MemoryHarness, TopicEmbedder

from yoreni.config.schema import MemoryConfig
from yoreni.memory.models import FactStatus
from yoreni.memory.service import MemoryService


async def _commit(harness: MemoryHarness, run_id: str, **draft_kwargs):
    harness.record(run_id, ("user", draft_kwargs.pop("evidence_text", "remember this")))
    source = harness.evidence_id(run_id)
    fact_id = harness.service.store.commit_candidates([
        harness.draft(sources=(source,), **draft_kwargs),
    ])[0]
    return fact_id


@pytest.fixture
def semantic_harness(tmp_path, memory_config: MemoryConfig) -> MemoryHarness:
    embedder = TopicEmbedder(
        {"python": [1.0, 0.0, 0.0], "chinese": [0.0, 1.0, 0.0], "rust": [0.0, 0.0, 1.0]},
        aliases={
            "programming": "python",
            "language": "python",
            "code": "python",
            "中文": "chinese",
            "汉语": "chinese",
        },
    )
    return MemoryHarness(
        MemoryService(tmp_path, memory_config, embedder=embedder), tmp_path,
    )


async def test_chinese_query_matches_at_character_granularity(harness):
    fact_id = await _commit(
        harness, "run-1",
        subject="user", predicate="workflow",
        text="用户习惯在提交前运行完整测试。",
    )

    hits = await harness.service.search("提交前运行测试")
    assert [hit.fact.id for hit in hits] == [fact_id]
    assert hits[0].signals == ("keyword",)
    assert hits[0].sources[0].run_id == "run-1"


async def test_english_prefix_query_matches(harness):
    fact_id = await _commit(
        harness, "run-1",
        subject="project:yoreni", predicate="language",
        text="The Yoreni gateway is written in Python.",
        kind="project_knowledge",
        scope="project:yoreni",
    )

    hits = await harness.service.search("Pyth")
    assert [hit.fact.id for hit in hits] == [fact_id]


async def test_mixed_language_query_matches(harness):
    fact_id = await _commit(
        harness, "run-1",
        subject="project:yoreni", predicate="stack",
        text="Yoreni 的 gateway 使用 Python 和 SQLite。",
        kind="project_knowledge",
    )

    hits = await harness.service.search("Python gateway")
    assert [hit.fact.id for hit in hits] == [fact_id]


async def test_semantic_only_match_is_fused_into_the_result(semantic_harness):
    fact_id = await _commit(
        semantic_harness, "run-1",
        subject="user", predicate="stack",
        text="The user writes Python every day.",
    )
    await semantic_harness.service.embed_missing()

    hits = await semantic_harness.service.search("which programming language")
    assert [hit.fact.id for hit in hits] == [fact_id]
    assert hits[0].signals == ("semantic",)


async def test_keyword_and_semantic_signals_combine(semantic_harness):
    fact_id = await _commit(
        semantic_harness, "run-1",
        subject="user", predicate="stack",
        text="The user writes Python every day.",
    )
    await semantic_harness.service.embed_missing()

    hits = await semantic_harness.service.search("Python programming language")
    assert hits[0].fact.id == fact_id
    assert set(hits[0].signals) == {"keyword", "semantic"}


async def test_scope_filter_keeps_global_and_the_named_project(harness):
    global_id = await _commit(
        harness, "run-1", subject="user", predicate="editor", text="The user uses Neovim.",
    )
    project_a = await _commit(
        harness, "run-2", subject="project:a", predicate="database", text="Project A uses SQLite.",
        kind="project_knowledge", scope="project:a",
    )
    project_b = await _commit(
        harness, "run-3", subject="project:b", predicate="database", text="Project B uses SQLite.",
        kind="project_knowledge", scope="project:b",
    )

    hits = await harness.service.search("SQLite", scope="project:a")
    ids = {hit.fact.id for hit in hits}
    assert ids == {project_a}
    assert project_b not in ids

    all_hits = await harness.service.search("SQLite")
    assert {hit.fact.id for hit in all_hits} == {project_a, project_b}
    assert global_id not in {hit.fact.id for hit in all_hits}


async def test_superseded_history_is_hidden_by_default_but_retrievable(harness):
    old_id = await _commit(
        harness, "run-1", text="The user prefers Chinese.", observed_at="2026-01-01T00:00:00+00:00",
    )
    new_id = await _commit(
        harness, "run-2", text="The user prefers English.", observed_at="2026-02-01T00:00:00+00:00",
    )

    current = await harness.service.search("prefers")
    assert [hit.fact.id for hit in current] == [new_id]

    history = await harness.service.search(
        "prefers", statuses=(FactStatus.SUPERSEDED,), scope=None,
    )
    assert [hit.fact.id for hit in history] == [old_id]
    assert history[0].fact.superseded_by == new_id


async def test_valid_at_returns_the_version_in_force_at_that_time(harness):
    old_id = await _commit(
        harness, "run-1", text="The user prefers Chinese.", observed_at="2026-01-01T00:00:00+00:00",
    )
    new_id = await _commit(
        harness, "run-2", text="The user prefers English.", observed_at="2026-06-01T00:00:00+00:00",
    )

    before = await harness.service.search(
        "prefers", statuses=(FactStatus.SUPERSEDED, FactStatus.ACTIVE),
        valid_at="2026-03-01T00:00:00+00:00",
    )
    assert [hit.fact.id for hit in before] == [old_id]

    after = await harness.service.search(
        "prefers", statuses=(FactStatus.SUPERSEDED, FactStatus.ACTIVE),
        valid_at="2026-07-01T00:00:00+00:00",
    )
    assert [hit.fact.id for hit in after] == [new_id]


async def test_duplicate_claims_are_returned_once(harness):
    first = await _commit(harness, "run-1", text="The user prefers Chinese.")
    harness.record("run-2", ("user", "I prefer Chinese."))
    second_source = harness.evidence_id("run-2")
    same = harness.service.store.commit_candidates([
        harness.draft(text="The user prefers Chinese.", sources=(second_source,)),
    ])[0]

    hits = await harness.service.search("Chinese")
    assert [hit.fact.id for hit in hits] == [first]
    assert same == first
    assert len(hits) == 1


async def test_no_match_returns_an_empty_result(harness):
    await _commit(harness, "run-1", text="The user prefers Chinese.")
    assert await harness.service.search("quantum chromodynamics") == []


async def test_kind_and_subject_filters_are_applied(harness):
    preference = await _commit(
        harness, "run-1", text="The user prefers Chinese.", kind="user_preference",
    )
    project = await _commit(
        harness, "run-2", subject="project:yoreni", predicate="docs_language",
        text="Yoreni project documentation is in Chinese.",
        kind="project_knowledge", scope="project:yoreni",
    )

    hits = await harness.service.search("Chinese", kinds=("project_knowledge",))
    assert [hit.fact.id for hit in hits] == [project]

    hits = await harness.service.search("Chinese", subjects=("user",))
    assert [hit.fact.id for hit in hits] == [preference]


async def test_fact_detail_includes_sources_relations_and_versions(harness):
    old_id = await _commit(
        harness, "run-1", text="The user prefers Chinese.", observed_at="2026-01-01T00:00:00+00:00",
    )
    new_id = await _commit(
        harness, "run-2", text="The user prefers English.", observed_at="2026-02-01T00:00:00+00:00",
    )

    detail = harness.service.fact_detail(new_id)
    assert detail is not None
    assert detail["fact"]["id"] == new_id
    assert detail["fact"]["status"] == "active"
    assert detail["sources"][0]["run_id"] == "run-2"
    assert {"from": new_id, "to": old_id, "relation": "supersedes"} in detail["relations"]
    assert [version["id"] for version in detail["versions"]] == [old_id, new_id]
    assert harness.service.fact_detail("fact_missing") is None


async def test_deleted_facts_never_appear_in_retrieval(harness):
    fact_id = await _commit(harness, "run-1", text="The user prefers Chinese.")
    harness.service.store.delete_fact(fact_id)

    assert await harness.service.search("Chinese") == []
    assert harness.service.fact_detail(fact_id) is None


async def test_a_changed_embedding_dimension_starts_a_separate_index(tmp_path):
    from yoreni.config.schema import MemoryConfig

    aliases = {"programming": "python", "language": "python", "code": "python"}
    first = MemoryService(
        tmp_path, MemoryConfig(),
        embedder=TopicEmbedder({"python": [1.0, 0.0]}, aliases=aliases),
    )
    harness = MemoryHarness(first, tmp_path)
    fact_id = harness.commit("run-1", subject="user", predicate="stack",
                             text="The user writes Python every day.")
    assert await first.embed_missing() == 1
    assert [hit.fact.id for hit in await first.search("which programming language")] == [fact_id]

    second = MemoryService(
        tmp_path, MemoryConfig(),
        embedder=TopicEmbedder({"python": [1.0, 0.0, 0.0]}, aliases=aliases),
    )
    # The old vectors belong to another index; pretending they matched would
    # look like "the user never said that".
    assert await second.search("which programming language") == []
    assert await second.embed_missing() == 1
    assert [hit.fact.id for hit in await second.search("which programming language")] == [fact_id]
