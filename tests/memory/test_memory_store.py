"""Storage contract: evidence, reconciliation, versioning, deletion, jobs."""

from __future__ import annotations

import threading

import pytest

from yoreni.memory.models import (
    FactKind,
    FactStatus,
    MemorySchemaError,
    SourceType,
    content_hash,
    dedupe_key,
    utc_now,
)
from yoreni.memory.service import build_run_evidence
from yoreni.memory.store import SCHEMA_VERSION, FactStore


def _evidence(harness, run_id: str, text: str = "I prefer Chinese.", role: str = "user"):
    harness.record(run_id, (role, text))
    return harness.evidence_id(run_id, role)


def test_evidence_ids_are_deterministic_and_idempotent(harness):
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "content": "Error: not found"},
    ]
    first = build_run_evidence("run-1", messages, observed_at="2026-01-02T03:04:05+00:00")
    second = build_run_evidence("run-1", messages, observed_at="2026-01-02T03:04:05+00:00")
    assert [item.id for item in first] == [item.id for item in second]
    assert harness.service.store.insert_evidence(first) == 3
    assert harness.service.store.insert_evidence(second) == 0
    assert harness.service.store.evidence(first[0].id) is not None


def test_multimodal_messages_store_text_markers_not_binary_payloads(harness):
    image_block = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }
    summary = harness.service.record_run("run-1", [{
        "role": "user",
        "content": [
            {"type": "text", "text": "[image: /tmp/a.png]"},
            image_block,
            {"type": "text", "text": "what is this?"},
        ],
    }])

    assert summary.recorded == 1
    [item] = harness.service.store.evidence_for_run("run-1")
    assert item.content == "[image: /tmp/a.png]\nwhat is this?"
    assert "base64" not in item.content


def test_evidence_provenance_never_treats_assistant_prose_as_a_tool_result(harness):
    messages = [
        {"role": "user", "content": "please delete the file"},
        {"role": "assistant", "content": "I deleted the file."},
        {"role": "tool", "name": "exec", "content": "Error: permission denied"},
        {"role": "tool", "name": "exec", "content": "deleted"},
    ]
    items = build_run_evidence("run-2", messages)
    user, assistant, failed_tool, ok_tool = items
    assert user.source_type is SourceType.USER_STATEMENT
    assert assistant.source_type is SourceType.AGENT_INFERENCE
    assert failed_tool.source_type is SourceType.UNVERIFIED
    assert ok_tool.source_type is SourceType.TOOL_RESULT
    assert failed_tool.trust.value == "untrusted"
    assert ok_tool.trust.value == "verified"


def test_commit_links_sources_and_updates_the_keyword_index(harness):
    evidence_id = _evidence(harness, "run-a")
    fact_id = harness.service.store.commit_candidates([harness.draft(sources=(evidence_id,))])[0]

    fact = harness.service.store.fact(fact_id)
    assert fact is not None
    assert fact.status is FactStatus.ACTIVE
    assert fact.kind is FactKind.USER_PREFERENCE
    sources = harness.service.store.sources_for([fact_id])[fact_id]
    assert [source.evidence_id for source in sources] == [evidence_id]
    assert sources[0].run_id == "run-a"
    hits = harness.service.store.search_fts(
        '"chinese"', where="facts.status='active'", params=[], limit=5,
    )
    assert [fact_id for fact_id, _rank in hits] == [fact_id]


def test_duplicate_text_keeps_one_fact_and_adds_the_new_source(harness):
    first_evidence = _evidence(harness, "run-1")
    second_evidence = _evidence(harness, "run-2")
    first = harness.service.store.commit_candidates([harness.draft(sources=(first_evidence,))])[0]
    second = harness.service.store.commit_candidates([harness.draft(sources=(second_evidence,))])[0]

    assert first == second
    assert len(harness.service.store.sources_for([first])[first]) == 2


def test_newer_statement_supersedes_and_keeps_history(harness):
    evidence_id = _evidence(harness, "run-1")
    old_id = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers Chinese.",
            sources=(evidence_id,),
            observed_at="2026-01-01T00:00:00+00:00",
        ),
    ])[0]

    new_evidence = _evidence(harness, "run-2", "Actually I prefer English.")
    new_id = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers English.",
            sources=(new_evidence,),
            observed_at="2026-02-01T00:00:00+00:00",
        ),
    ])[0]

    old = harness.service.store.fact(old_id)
    new = harness.service.store.fact(new_id)
    assert old is not None and new is not None
    assert new.status is FactStatus.ACTIVE
    assert old.status is FactStatus.SUPERSEDED
    assert old.superseded_by == new.id
    assert old.valid_to is not None
    versions = harness.service.store.versions_for(new)
    assert [version.id for version in versions] == [old_id, new_id]


def test_inference_cannot_overwrite_a_user_statement(harness):
    evidence_id = _evidence(harness, "run-1")
    stated = harness.service.store.commit_candidates([
        harness.draft(text="The user prefers Chinese.", sources=(evidence_id,)),
    ])[0]

    inferred = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers French.",
            sources=(evidence_id,),
            source_type=SourceType.AGENT_INFERENCE,
            observed_at="2027-01-01T00:00:00+00:00",
        ),
    ])[0]

    assert harness.service.store.fact(stated).status is FactStatus.ACTIVE
    conflicting = harness.service.store.fact(inferred)
    assert conflicting is not None
    assert conflicting.status is FactStatus.DISPUTED
    relations = harness.service.store.relations_for(inferred)
    assert {"from": inferred, "to": stated, "relation": "conflicts_with"} in relations


def test_correcting_an_older_version_while_another_is_current_is_disputed(harness):
    evidence_id = _evidence(harness, "run-1")
    first = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers Chinese.",
            sources=(evidence_id,),
            observed_at="2026-01-01T00:00:00+00:00",
        ),
    ])[0]
    second = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers English.",
            sources=(evidence_id,),
            observed_at="2026-05-01T00:00:00+00:00",
        ),
    ])[0]

    third = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers Japanese.",
            sources=(evidence_id,),
            supersedes=first,
            observed_at="2026-06-01T00:00:00+00:00",
        ),
    ])[0]

    assert harness.service.store.fact(second).status is FactStatus.ACTIVE
    assert harness.service.store.fact(third).status is FactStatus.DISPUTED
    assert {relation["relation"] for relation in harness.service.store.relations_for(third)} == {
        "supersedes", "conflicts_with",
    }


def test_user_correction_always_supersedes(harness):
    evidence_id = _evidence(harness, "run-1")
    first = harness.service.store.commit_candidates([
        harness.draft(text="The user prefers Chinese.", sources=(evidence_id,)),
    ])[0]
    corrected = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers English.",
            sources=(evidence_id,),
            source_type=SourceType.USER_CORRECTION,
            supersedes=first,
            observed_at="2020-01-01T00:00:00+00:00",
        ),
    ])[0]

    assert harness.service.store.fact(corrected).status is FactStatus.ACTIVE
    assert harness.service.store.fact(first).status is FactStatus.SUPERSEDED


def test_multi_valued_facts_coexist_without_superseding(harness):
    evidence_id = _evidence(harness, "run-1")
    first = harness.service.store.commit_candidates([
        harness.draft(
            text="The user's first deploy failed on a missing certificate.",
            kind="event_experience",
            predicate="deploy_lesson",
            sources=(evidence_id,),
        ),
    ])[0]
    second = harness.service.store.commit_candidates([
        harness.draft(
            text="The user's second deploy needed a longer timeout.",
            kind="event_experience",
            predicate="deploy_lesson",
            sources=(evidence_id,),
        ),
    ])[0]

    assert first != second
    assert harness.service.store.fact(first).status is FactStatus.ACTIVE
    assert harness.service.store.fact(second).status is FactStatus.ACTIVE


def test_fact_without_evidence_is_rejected(harness):
    with pytest.raises(MemorySchemaError, match="no evidence"):
        harness.service.store.commit_candidates([harness.draft()])


def test_unknown_evidence_is_rejected(harness):
    with pytest.raises(MemorySchemaError, match="unknown evidence"):
        harness.service.store.commit_candidates([harness.draft(sources=("ev_missing",))])


def test_merge_moves_sources_and_retires_duplicates(harness):
    first_evidence = _evidence(harness, "run-1", "I like dark mode.")
    second_evidence = _evidence(harness, "run-2", "Dark theme is my preference.")
    keep = harness.service.store.commit_candidates([
        harness.draft(subject="ui", predicate="theme", text="Dark mode.", sources=(first_evidence,)),
    ])[0]
    duplicate = harness.service.store.commit_candidates([
        harness.draft(
            subject="ui", predicate="theme", text="Dark theme.", sources=(second_evidence,),
        ),
    ])[0]

    assert harness.service.store.merge_facts([duplicate], keep=keep) == keep
    merged = harness.service.store.fact(duplicate)
    assert merged is not None
    assert merged.status is FactStatus.SUPERSEDED
    assert merged.superseded_by == keep
    source_ids = {
        source.evidence_id for source in harness.service.store.sources_for([keep])[keep]
    }
    assert source_ids == {first_evidence, second_evidence}


def test_delete_removes_the_whole_version_chain(harness):
    evidence_id = _evidence(harness, "run-1")
    old_id = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers Chinese.",
            sources=(evidence_id,),
            observed_at="2026-01-01T00:00:00+00:00",
        ),
    ])[0]
    new_id = harness.service.store.commit_candidates([
        harness.draft(
            text="The user prefers English.",
            sources=(evidence_id,),
            observed_at="2026-02-01T00:00:00+00:00",
        ),
    ])[0]

    harness.service.store.delete_fact(old_id)

    assert harness.service.store.fact(old_id) is None
    assert harness.service.store.fact(new_id) is None
    assert harness.service.store.search_fts(
        '"prefers"', where="facts.status!='deleted'", params=[], limit=5,
    ) == []


def test_delete_keeps_evidence_another_fact_still_cites(harness):
    evidence_id = _evidence(harness, "run-1", "I live in Berlin and use Neovim.")
    city = harness.service.store.commit_candidates([
        harness.draft(subject="user", predicate="city", text="The user lives in Berlin.",
                      sources=(evidence_id,)),
    ])[0]
    editor = harness.service.store.commit_candidates([
        harness.draft(subject="user", predicate="editor", text="The user uses Neovim.",
                      sources=(evidence_id,)),
    ])[0]

    purged = harness.service.store.delete_fact(city)

    assert purged == []
    item = harness.service.store.evidence([evidence_id])[evidence_id]
    assert item.suppressed is False and item.content
    assert harness.service.store.sources_for([editor])[editor][0].evidence_id == evidence_id


def test_delete_removes_indexes_sources_and_source_text(harness):
    evidence_id = _evidence(harness, "run-1", "My secret is 1234.")
    fact_id = harness.service.store.commit_candidates([
        harness.draft(text="The user's secret is 1234.", sources=(evidence_id,)),
    ])[0]

    harness.service.store.delete_fact(fact_id, purge_evidence=True)

    assert harness.service.store.fact(fact_id) is None
    assert harness.service.store.search_fts(
        '"secret"', where="facts.status!='deleted'", params=[], limit=5,
    ) == []
    item = harness.service.store.evidence([evidence_id])[evidence_id]
    assert item.suppressed is True and item.content == ""
    assert harness.service.store.sources_for([fact_id]) == {fact_id: []}


def test_forget_tombstone_blocks_rederivation_from_old_evidence_but_not_a_new_statement(harness):
    linked = _evidence(harness, "run-1", "My favourite colour is teal.")
    unlinked = _evidence(harness, "run-2", "My favourite colour is teal.")
    fact_id = harness.service.store.commit_candidates([
        harness.draft(
            subject="user", predicate="favourite_colour",
            text="The user's favourite colour is teal.", sources=(linked,),
        ),
    ])[0]
    harness.service.store.delete_fact(fact_id)

    # A different record of the same old statement must not resurrect the fact
    # through a later extraction or Dream pass.
    resurrected = harness.service.store.commit_candidates([
        harness.draft(
            subject="user", predicate="favourite_colour",
            text="The user's favourite colour is teal.", sources=(unlinked,),
        ),
    ])
    assert resurrected == []

    # A genuine restatement creates newer evidence and is allowed.
    new_evidence = _evidence(harness, "run-3", "My favourite colour is teal.")
    allowed = harness.service.store.commit_candidates([
        harness.draft(
            subject="user", predicate="favourite_colour",
            text="The user's favourite colour is teal.", sources=(new_evidence,),
        ),
    ])
    assert len(allowed) == 1


def test_store_reopens_with_the_same_state(tmp_path, harness):
    evidence_id = _evidence(harness, "run-1")
    fact_id = harness.service.store.commit_candidates([
        harness.draft(sources=(evidence_id,)),
    ])[0]

    reopened = FactStore(tmp_path / "memory" / "memory.sqlite3")
    assert reopened.meta_get("schema_version") == str(SCHEMA_VERSION)
    fact = reopened.fact(fact_id)
    assert fact is not None and fact.text == "The user prefers Chinese."
    assert reopened.sources_for([fact_id])[fact_id][0].evidence_id == evidence_id


def test_reconciliation_is_atomic_when_a_draft_is_invalid(harness):
    evidence_id = _evidence(harness, "run-1")
    valid = harness.draft(subject="user", predicate="city", text="The user lives in Berlin.",
                          sources=(evidence_id,))
    invalid = harness.draft(subject="user", predicate="job", text="The user is a pilot.",
                            sources=("ev_missing",))

    with pytest.raises(MemorySchemaError):
        harness.service.store.commit_candidates([valid, invalid])

    assert harness.service.store.stats().active_facts == 0


def test_jobs_are_idempotent_and_retry_after_failure(harness):
    store = harness.service.store
    job_id = store.enqueue_job("extract", "extract:run-1", run_id="run-1")
    assert store.enqueue_job("extract", "extract:run-1", run_id="run-1") == job_id
    assert store.job_counts() == (1, 0)

    [claimed] = store.claim_jobs("extract", 1)
    assert claimed["id"] == job_id and claimed["attempts"] == 1
    assert store.job_counts() == (1, 0)

    store.fail_job(job_id, "provider timeout")
    assert store.job_counts() == (0, 1)

    [retried] = store.claim_jobs("extract", 1)
    assert retried["attempts"] == 2
    store.complete_job(job_id)
    assert store.job_counts() == (0, 0)
    assert store.claim_jobs("extract", 1) == []


def test_stale_processing_jobs_return_to_the_queue(harness):
    store = harness.service.store
    store.enqueue_job("extract", "extract:run-1", run_id="run-1")
    store.claim_jobs("extract", 1)

    assert store.reset_stale_jobs(older_than_seconds=3600) == 0
    assert store.job_counts() == (1, 0)
    assert store.reset_stale_jobs(older_than_seconds=0) == 1
    assert store.job_counts() == (1, 0)


def test_concurrent_commits_in_one_second_keep_distinct_versions(harness):
    evidence_id = _evidence(harness, "run-1")
    store = harness.service.store
    created: list[str] = []
    errors: list[BaseException] = []

    def commit(text: str) -> None:
        try:
            created.extend(store.commit_candidates([
                harness.draft(
                    subject="user", predicate="mood", text=text,
                    kind="event_experience", sources=(evidence_id,),
                ),
            ]))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=commit, args=(f"The user felt {name}.",))
        for name in ("calm", "tired", "curious")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(created) == 3
    assert len(set(created)) == 3
    assert store.stats().active_facts == 3


def test_schema_rejects_a_newer_database_version(tmp_path):
    import sqlite3

    path = tmp_path / "memory" / "memory.sqlite3"
    FactStore(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")

    with pytest.raises(MemorySchemaError, match="schema 99"):
        FactStore(path)


def test_dedupe_key_is_stable_for_case_and_spacing():
    assert dedupe_key("Global", " User ", "Preferred Language") == dedupe_key(
        "global", "user", "preferred_language",
    )
    assert content_hash("abc") != content_hash("abc ")


def test_timestamps_never_invent_a_time_for_unknown_inputs():
    from yoreni.memory.models import normalize_timestamp

    assert normalize_timestamp(None) is None
    assert normalize_timestamp("not a date") is None
    assert normalize_timestamp("2026-03-01") == "2026-03-01T00:00:00+00:00"
    assert normalize_timestamp(utc_now()) is not None


def test_oversized_fact_text_is_rejected_instead_of_truncated(harness):
    evidence_id = _evidence(harness, "run-1")

    with pytest.raises(MemorySchemaError, match="exceeds"):
        harness.service.store.commit_candidates([harness.draft(
            text="x" * 2_001, sources=(evidence_id,),
        )])

    assert harness.service.store.stats().active_facts == 0


def test_a_new_value_supersedes_a_dormant_version(harness):
    evidence_id = _evidence(harness, "run-1")
    old = harness.service.store.commit_candidates([harness.draft(
        subject="user", predicate="city", text="The user lives in London.",
        kind="user_fact", sources=(evidence_id,),
    )])[0]
    harness.service.store.set_status(old, FactStatus.DORMANT)

    new = harness.service.store.commit_candidates([harness.draft(
        subject="user", predicate="city", text="The user lives in Berlin.",
        kind="user_fact", sources=(evidence_id,),
        observed_at="2027-01-01T00:00:00+00:00",
    )])[0]

    assert harness.service.store.fact(old).status is FactStatus.SUPERSEDED
    assert harness.service.store.fact(new).status is FactStatus.ACTIVE
