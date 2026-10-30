"""Fixed long-term capability scenarios, one test per required behaviour."""

from __future__ import annotations

import json

from memory_helpers import ScriptedProvider

from yoreni.memory.dream import DreamConsolidator
from yoreni.memory.models import FactStatus, SourceType
from yoreni.providers.base import LLMResponse
from yoreni.utils.llm_runtime import LLMRuntime


async def test_single_fact_recall_in_chinese(harness):
    harness.record("run-1", ("user", "我对花生过敏。"))
    source = harness.evidence_id("run-1")
    fact_id = harness.service.store.commit_candidates([harness.draft(
        subject="user", predicate="allergy", text="用户对花生过敏。",
        kind="user_fact", sources=(source,),
    )])[0]

    hits = await harness.service.search("花生过敏")
    assert [hit.fact.id for hit in hits] == [fact_id]
    assert hits[0].fact.kind.value == "user_fact"


async def test_cross_event_synthesis_combines_two_evidence_items(harness):
    harness.record("run-1", ("user", "I am moving to Berlin in June."))
    harness.record("run-2", ("user", "I signed a lease in Prenzlauer Berg."))
    first, second = harness.evidence_id("run-1"), harness.evidence_id("run-2")
    harness.record("run-3", ("user", "Moving plans are settled."))
    harness.commit("run-4", subject="user", predicate="editor", text="The user uses Neovim.")
    provider = ScriptedProvider(LLMResponse(content=json.dumps({"operations": [{
        "op": "add_fact",
        "subject": "user",
        "predicate": "home",
        "text": "The user is moving to Berlin and has signed a lease in Prenzlauer Berg.",
        "kind": "event_experience",
        "scope": "global",
        "evidence": [first, second],
    }]})))
    runtime = LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)

    result = await DreamConsolidator(harness.service).consolidate(runtime)

    assert result.status == "completed"
    fact = next(
        item for item in harness.service.store.recent_by_status(FactStatus.ACTIVE, 10)
        if item.predicate == "home"
    )
    sources = harness.service.store.sources_for([fact.id])[fact.id]
    assert {source.evidence_id for source in sources} == {first, second}


async def test_preference_update_returns_only_the_current_value(harness):
    harness.record("run-1", ("user", "I prefer Chinese."))
    first = harness.evidence_id("run-1")
    old = harness.service.store.commit_candidates([harness.draft(
        text="The user prefers Chinese.", sources=(first,),
        observed_at="2026-01-01T00:00:00+00:00",
    )])[0]
    harness.record("run-2", ("user", "Actually English."))
    second = harness.evidence_id("run-2")
    new = harness.service.store.commit_candidates([harness.draft(
        text="The user prefers English.", sources=(second,),
        observed_at="2026-02-01T00:00:00+00:00",
    )])[0]

    hits = await harness.service.search("prefers language")
    assert [hit.fact.id for hit in hits] == [new]
    assert harness.service.store.fact(old).status is FactStatus.SUPERSEDED


async def test_decision_reversal_keeps_the_old_decision_as_history(harness):
    harness.record("run-1", ("user", "We will use SQLite."))
    first = harness.evidence_id("run-1")
    old = harness.service.store.commit_candidates([harness.draft(
        subject="project:yoreni", predicate="database", text="The project uses SQLite.",
        kind="decision", sources=(first,), observed_at="2026-01-01T00:00:00+00:00",
    )])[0]
    harness.record("run-2", ("user", "We switched to Postgres."))
    second = harness.evidence_id("run-2")
    new = harness.service.store.commit_candidates([harness.draft(
        subject="project:yoreni", predicate="database", text="The project uses Postgres.",
        kind="decision", sources=(second,), observed_at="2026-02-01T00:00:00+00:00",
    )])[0]

    assert harness.service.store.fact(old).status is FactStatus.SUPERSEDED
    current = await harness.service.search("database", scope="project:yoreni")
    assert [hit.fact.id for hit in current] == [new]
    history = await harness.service.search(
        "database", statuses=(FactStatus.SUPERSEDED,), scope="project:yoreni",
    )
    assert [hit.fact.id for hit in history] == [old]


async def test_temporal_relations_return_the_version_in_force(harness):
    harness.record("run-1", ("user", "I live in London."))
    first = harness.evidence_id("run-1")
    old = harness.service.store.commit_candidates([harness.draft(
        subject="user", predicate="city", text="The user lives in London.",
        kind="user_fact", sources=(first,), observed_at="2026-01-01T00:00:00+00:00",
    )])[0]
    harness.record("run-2", ("user", "I moved to Berlin."))
    second = harness.evidence_id("run-2")
    new = harness.service.store.commit_candidates([harness.draft(
        subject="user", predicate="city", text="The user lives in Berlin.",
        kind="user_fact", sources=(second,), observed_at="2026-06-01T00:00:00+00:00",
    )])[0]

    statuses = (FactStatus.ACTIVE, FactStatus.SUPERSEDED)
    before = await harness.service.search(
        "lives", statuses=statuses, valid_at="2026-03-01T00:00:00+00:00",
    )
    after = await harness.service.search(
        "lives", statuses=statuses, valid_at="2026-07-01T00:00:00+00:00",
    )
    assert [hit.fact.id for hit in before] == [old]
    assert [hit.fact.id for hit in after] == [new]


async def test_conflicting_claims_stay_disputed_instead_of_guessing(harness):
    harness.record("run-1", ("user", "I prefer Chinese."))
    evidence = harness.evidence_id("run-1")
    stated = harness.service.store.commit_candidates([harness.draft(
        text="The user prefers Chinese.", sources=(evidence,),
    )])[0]
    inferred = harness.service.store.commit_candidates([harness.draft(
        text="The user prefers French.", sources=(evidence,),
        source_type=SourceType.AGENT_INFERENCE,
        observed_at="2027-01-01T00:00:00+00:00",
    )])[0]

    assert harness.service.store.fact(stated).status is FactStatus.ACTIVE
    assert harness.service.store.fact(inferred).status is FactStatus.DISPUTED
    disputed = await harness.service.search(
        "French", statuses=(FactStatus.DISPUTED,),
    )
    assert [hit.fact.id for hit in disputed] == [inferred]


async def test_unknown_question_is_answered_with_no_memory(harness):
    harness.record("run-1", ("user", "I prefer Chinese."))
    harness.commit("run-2", text="The user prefers Chinese.")

    assert await harness.service.search("What is my blood type?") == []
    assert await harness.service.context_block("What is my blood type?") is None


async def test_stale_knowledge_is_dormant_not_deleted(harness):
    harness.record("run-1", ("user", "I work at Acme."))
    evidence = harness.evidence_id("run-1")
    fact_id = harness.service.store.commit_candidates([harness.draft(
        subject="user", predicate="employer", text="The user works at Acme.",
        kind="user_fact", sources=(evidence,),
    )])[0]
    harness.service.store.set_status(fact_id, FactStatus.DORMANT)

    assert await harness.service.search("Acme") == []
    dormant = await harness.service.search("Acme", statuses=(FactStatus.DORMANT,))
    assert [hit.fact.id for hit in dormant] == [fact_id]
    assert harness.service.store.fact(fact_id) is not None
