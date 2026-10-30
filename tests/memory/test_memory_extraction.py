"""Extraction: strict parsing, provenance, job lifecycle and failure visibility."""

from __future__ import annotations

import pytest
from memory_helpers import ScriptedProvider, TopicEmbedder, facts_payload

from yoreni.memory.extraction import FactExtractor, parse_candidates
from yoreni.memory.models import (
    FactKind,
    MemoryExtractionError,
    MemorySchemaError,
    SourceType,
)
from yoreni.providers.base import LLMResponse
from yoreni.utils.llm_runtime import LLMRuntime


def _runtime(provider: ScriptedProvider) -> LLMRuntime:
    return LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)


async def test_extraction_commits_a_cited_candidate(harness):
    harness.record("run-1", ("user", "My preferred language is Chinese."))
    evidence_id = harness.evidence_id("run-1")
    provider = ScriptedProvider(LLMResponse(content=facts_payload({
        "subject": "user",
        "predicate": "preferred_language",
        "text": "The user prefers Chinese.",
        "kind": "user_preference",
        "scope": "global",
        "sources": [evidence_id],
        "confidence": 0.8,
    })))

    result = await FactExtractor(harness.service.store).extract(
        harness.service.store.evidence_for_run("run-1"),
        runtime=_runtime(provider),
    )

    assert result.candidates == 1
    [fact_id] = result.fact_ids
    fact = harness.service.store.fact(fact_id)
    assert fact is not None
    assert fact.kind is FactKind.USER_PREFERENCE
    assert fact.source_type is SourceType.USER_STATEMENT
    assert fact.observed_at is not None
    assert harness.service.store.sources_for([fact_id])[fact_id][0].evidence_id == evidence_id
    assert evidence_id in provider.calls[0][0]["content"]


def test_parse_rejects_a_world_fact_cited_to_the_users_request(harness):
    harness.record("run-1", ("user", "Please look up the latest version."))
    evidence = harness.service.store.evidence_for_run("run-1")

    with pytest.raises(MemorySchemaError, match="verified tool result"):
        parse_candidates(facts_payload({
            "subject": "project:yoreni",
            "predicate": "version",
            "text": "Yoreni is on version 2.4.1.",
            "kind": "project_knowledge",
            "sources": [evidence[0].id],
        }), evidence, 20)


async def test_assistant_prose_is_only_an_inference(harness):
    harness.record("run-1", ("user", "did you delete it?"), ("assistant", "I deleted the file."))
    assistant_evidence = harness.evidence_id("run-1", "assistant")
    provider = ScriptedProvider(LLMResponse(content=facts_payload({
        "subject": "project:demo",
        "predicate": "cleanup_done",
        "text": "The demo file was deleted.",
        "kind": "event_experience",
        "sources": [assistant_evidence],
    })))

    result = await FactExtractor(harness.service.store).extract(
        harness.service.store.evidence_for_run("run-1"),
        runtime=_runtime(provider),
    )

    fact = harness.service.store.fact(result.fact_ids[0])
    assert fact is not None
    assert fact.source_type is SourceType.AGENT_INFERENCE


async def test_a_verified_tool_result_establishes_provenance(harness):
    harness.record(
        "run-1",
        ("user", "check the branch"),
        ("tool", "Error: detached HEAD"),
        ("tool", "main"),
    )
    tool_evidence = harness.evidence_id("run-1", "tool", index=1)
    provider = ScriptedProvider(LLMResponse(content=facts_payload({
        "subject": "project:demo",
        "predicate": "current_branch",
        "text": "The project is on the main branch.",
        "kind": "project_knowledge",
        "sources": [tool_evidence],
    })))

    result = await FactExtractor(harness.service.store).extract(
        harness.service.store.evidence_for_run("run-1"),
        runtime=_runtime(provider),
    )

    fact = harness.service.store.fact(result.fact_ids[0])
    assert fact is not None
    assert fact.source_type is SourceType.TOOL_RESULT


async def test_a_failed_tool_result_is_not_trusted(harness):
    harness.record("run-1", ("user", "delete it"), ("tool", "Error: permission denied"))
    tool_evidence = harness.evidence_id("run-1", "tool")
    provider = ScriptedProvider(LLMResponse(content=facts_payload({
        "subject": "project:demo",
        "predicate": "cleanup_done",
        "text": "Deleting the file failed with permission denied.",
        "kind": "event_experience",
        "sources": [tool_evidence],
    })))

    result = await FactExtractor(harness.service.store).extract(
        harness.service.store.evidence_for_run("run-1"),
        runtime=_runtime(provider),
    )

    fact = harness.service.store.fact(result.fact_ids[0])
    assert fact is not None
    assert fact.source_type is SourceType.UNVERIFIED


def test_parse_rejects_unknown_evidence(harness):
    harness.record("run-1", ("user", "hello"))
    evidence = harness.service.store.evidence_for_run("run-1")

    with pytest.raises(MemorySchemaError, match="not provided"):
        parse_candidates(facts_payload({
            "subject": "user", "predicate": "name", "text": "The user is Ada.",
            "kind": "user_fact", "sources": ["ev_invented"],
        }), evidence, 20)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '{"facts": {}}',
        '{"facts": [{"subject": "user"}]}',
        '{"facts": [{"subject": "user", "predicate": "x", "text": "y", '
        '"kind": "made_up", "sources": []}]}',
    ],
)
def test_parse_never_guesses_on_malformed_output(harness, raw):
    harness.record("run-1", ("user", "hello"))
    evidence = harness.service.store.evidence_for_run("run-1")

    with pytest.raises(MemorySchemaError):
        parse_candidates(raw, evidence, 20)


def test_candidate_limit_bounds_one_batch(harness):
    harness.record("run-1", ("user", "hello"))
    evidence = harness.service.store.evidence_for_run("run-1")
    payload = facts_payload(*[
        {
            "subject": "user", "predicate": f"p{index}", "text": f"Fact {index}.",
            "kind": "user_fact", "sources": [evidence[0].id],
        }
        for index in range(5)
    ])

    drafts = parse_candidates(payload, evidence, 2)
    assert [draft.predicate for draft in drafts] == ["p0", "p1"]


async def test_failed_extraction_keeps_evidence_and_the_job_retryable(harness):
    harness.record("run-1", ("user", "I prefer Chinese."))
    evidence_id = harness.evidence_id("run-1")
    provider = ScriptedProvider(
        LLMResponse(content="not json"),
        LLMResponse(content=facts_payload({
            "subject": "user",
            "predicate": "preferred_language",
            "text": "The user prefers Chinese.",
            "kind": "user_preference",
            "sources": [evidence_id],
        })),
    )

    assert await harness.service.extract_pending(_runtime(provider), limit=1) == 0
    assert harness.service.store.job_counts() == (0, 1)
    assert harness.service.store.evidence_for_run("run-1")[0].id == evidence_id
    assert harness.service.stats()["active_facts"] == 0

    assert await harness.service.extract_pending(_runtime(provider), limit=1) == 1
    assert harness.service.store.job_counts() == (0, 0)
    assert harness.service.stats()["active_facts"] == 1


async def test_provider_error_is_an_explicit_extraction_failure(harness):
    harness.record("run-1", ("user", "I prefer Chinese."))
    provider = ScriptedProvider(LLMResponse(content=None, finish_reason="error"))

    with pytest.raises(MemoryExtractionError, match="provider error"):
        await FactExtractor(harness.service.store).extract(
            harness.service.store.evidence_for_run("run-1"),
            runtime=_runtime(provider),
        )


async def test_assistant_only_runs_do_not_queue_extraction(harness):
    summary = harness.service.record_run("run-1", [{"role": "assistant", "content": "done"}])

    assert summary.recorded == 1
    assert summary.job_id is None
    assert harness.service.store.job_counts() == (0, 0)


async def test_an_embedding_failure_does_not_claim_a_clean_extraction(harness):
    class BrokenEmbedder:
        settings = type("Settings", (), {"model": "broken", "dimensions": 2})()

        async def embed(self, texts):
            from yoreni.memory.embeddings import EmbeddingError

            raise EmbeddingError("embedding endpoint unavailable")

    harness.record("run-1", ("user", "I prefer Chinese."))
    evidence_id = harness.evidence_id("run-1")
    harness.service.embedder = BrokenEmbedder()
    provider = ScriptedProvider(LLMResponse(content=facts_payload({
        "subject": "user",
        "predicate": "preferred_language",
        "text": "The user prefers Chinese.",
        "kind": "user_preference",
        "sources": [evidence_id],
    })))

    assert await harness.service.extract_pending(_runtime(provider), limit=1) == 0
    assert harness.service.store.job_counts() == (0, 1)
    assert harness.service.stats()["active_facts"] == 1

    # The missing vector is recoverable without re-running extraction.
    harness.service.embedder = TopicEmbedder({"chinese": [1.0, 0.0]})
    assert await harness.service.embed_missing() == 1


async def test_long_runs_are_extracted_in_chunks_without_dropping_evidence(harness):
    messages = [{"role": "user", "content": f"Fact number {index}."} for index in range(61)]
    harness.service.record_run("run-1", messages)
    evidence = harness.service.store.evidence_for_run("run-1")
    assert len(evidence) == 61
    first_chunk, second_chunk = evidence[:60], evidence[60:]
    provider = ScriptedProvider(
        LLMResponse(content=facts_payload({
            "subject": "user", "predicate": "first_chunk",
            "text": "The first chunk was read.", "kind": "user_fact",
            "sources": [first_chunk[0].id],
        })),
        LLMResponse(content=facts_payload({
            "subject": "user", "predicate": "second_chunk",
            "text": "The second chunk was read.", "kind": "user_fact",
            "sources": [second_chunk[0].id],
        })),
    )

    result = await FactExtractor(harness.service.store).extract(
        evidence, runtime=_runtime(provider),
    )

    assert len(provider.calls) == 2
    assert len(result.fact_ids) == 2
    assert {harness.service.store.fact(fact_id).predicate for fact_id in result.fact_ids} == {
        "first_chunk", "second_chunk",
    }
