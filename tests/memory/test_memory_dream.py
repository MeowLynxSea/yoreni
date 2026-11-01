"""Dream consolidation: validated change sets, recovery and restore."""

from __future__ import annotations

import asyncio
import json

import pytest
from memory_helpers import MemoryHarness, ScriptedProvider

from yoreni.memory.core import SOUL_PATH, USER_PATH
from yoreni.memory.dream import MAX_TOOL_ROUNDS, DreamConsolidator, validate_operations
from yoreni.memory.models import DreamError, FactStatus
from yoreni.providers.base import LLMResponse, ToolCallRequest
from yoreni.utils.llm_runtime import LLMRuntime


def _runtime(*responses: LLMResponse) -> LLMRuntime:
    return LLMRuntime.capture(
        ScriptedProvider(*responses), "test-model", context_window_tokens=128_000,
    )


def _proposal(*operations: dict) -> LLMResponse:
    return LLMResponse(content=json.dumps({"operations": list(operations)}, ensure_ascii=False))


@pytest.fixture
def site(harness: MemoryHarness) -> MemoryHarness:
    harness.service.git.init()
    return harness


def _cursor(harness: MemoryHarness) -> str | None:
    return harness.service.store.meta_get("dream_fact_cursor")


async def test_noop_consolidation_advances_the_cursor_without_changes(site):
    site.commit("run-1", text="The user prefers Chinese.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal()))

    assert result.status == "completed"
    assert result.changes == 0
    assert _cursor(site) == str(site.service.store.max_fact_rowid())
    assert site.service.stats()["last_dream_status"] == "completed"
    assert site.service.core.read_user() == ""


async def test_add_fact_uses_cited_evidence_and_stays_inside_the_store(site):
    site.record("run-1", ("user", "I moved to Berlin last month."))
    evidence_id = site.evidence_id("run-1")
    site.commit("run-2", subject="user", predicate="editor", text="The user uses Neovim.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "add_fact",
        "subject": "user",
        "predicate": "city",
        "text": "The user lives in Berlin.",
        "kind": "user_fact",
        "scope": "global",
        "evidence": [evidence_id],
    })))

    assert result.status == "completed"
    assert result.changes == 1
    facts = site.service.store.recent_by_status(FactStatus.ACTIVE, 10)
    city = next(fact for fact in facts if fact.predicate == "city")
    assert city.source_type.value == "user_statement"
    assert site.service.fact_detail(city.id)["sources"][0]["evidence_id"] == evidence_id
    # The batch covers facts that existed when it was collected; the fact this
    # pass created is picked up by the next pass.
    assert _cursor(site) == "1"
    assert site.service.stats()["unconsolidated_facts"] == 1


async def test_update_core_writes_files_and_commits_a_version(site):
    site.commit("run-1", text="The user prefers Chinese.")
    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "update_core",
        "file": USER_PATH,
        "content": "- The user prefers Chinese.\n",
    })))

    assert result.status == "completed"
    assert site.service.core.read_user() == "- The user prefers Chinese.\n"
    record = site.service.store.dream_run(result.run_id)
    assert record is not None
    assert record["status"] == "completed"
    assert record["git_commit"]
    assert record["applied"]["files"][USER_PATH] == site.service.core.hash(USER_PATH)
    versions = site.service.store.dream_versions()
    assert [item["id"] for item in versions] == [result.run_id]
    commit = site.service.git.log()[0]
    assert "USER.md: +1 -0" in commit.message


async def test_consolidation_maintains_identity(site):
    site.workspace.joinpath(SOUL_PATH).write_text("original identity", encoding="utf-8")
    site.commit("run-1", text="The user prefers brief replies.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "update_core",
        "file": SOUL_PATH,
        "content": "You answer briefly.",
    })))

    assert result.status == "completed"
    assert site.workspace.joinpath(SOUL_PATH).read_text(encoding="utf-8") == "You answer briefly."
    assert site.service.store.dream_run(result.run_id)["status"] == "completed"
    assert "SOUL.md" in site.service.git.log()[0].message


async def test_add_fact_must_cite_existing_evidence(site):
    site.commit("run-1", text="The user prefers Chinese.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "add_fact",
        "subject": "user",
        "predicate": "city",
        "text": "The user lives in Berlin.",
        "kind": "user_fact",
        "evidence": ["ev_invented"],
    })))

    assert result.status == "failed"
    assert "unknown or deleted evidence" in result.detail
    assert site.service.stats()["active_facts"] == 1


@pytest.mark.parametrize("status", ["superseded", "deleted", "disputed"])
async def test_dream_cannot_force_a_lifecycle_transition(site, status):
    site.commit("run-1", text="The user prefers Chinese.")
    fact = site.service.store.recent_by_status(FactStatus.ACTIVE, 1)[0]

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "set_status", "fact_id": fact.id, "status": status,
    })))

    assert result.status == "failed"
    assert site.service.store.fact(fact.id).status is FactStatus.ACTIVE


async def test_dream_can_mark_a_fact_dormant_or_active(site):
    site.commit("run-1", text="The user prefers Chinese.", kind="user_preference")
    fact = site.service.store.recent_by_status(FactStatus.ACTIVE, 1)[0]

    dormant = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "set_status", "fact_id": fact.id, "status": "dormant",
    })))
    assert dormant.status == "completed"
    assert site.service.store.fact(fact.id).status is FactStatus.DORMANT

    site.commit("run-2", subject="user", predicate="editor", text="The user uses Neovim.")
    active = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "set_status", "fact_id": fact.id, "status": "active",
    })))
    assert active.status == "completed"
    assert site.service.store.fact(fact.id).status is FactStatus.ACTIVE


async def test_merge_facts_preserves_evidence_and_retires_duplicates(site):
    first = site.commit("run-1", subject="ui", predicate="theme", text="Dark mode.")
    second = site.commit("run-2", subject="ui", predicate="theme", text="Dark theme.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "merge_facts", "keep": first, "merge": [second],
    })))

    assert result.status == "completed"
    assert site.service.store.fact(second).status is FactStatus.SUPERSEDED
    sources = site.service.store.sources_for([first])[first]
    assert {source.run_id for source in sources} == {"run-1", "run-2"}


async def test_skill_creation_is_only_a_pending_proposal(site):
    site.commit("run-1", text="The user always runs tests before committing.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "propose_skill",
        "name": "test-first",
        "description": "Run the test suite before committing.",
        "body": "1. Run pytest.\n2. Only commit when it passes.",
    })))

    assert result.status == "completed"
    [proposal] = site.service.store.skill_proposals()
    assert proposal["name"] == "test-first"
    assert not (site.workspace / "skills" / "test-first").exists()


async def test_invalid_proposal_fails_without_advancing_the_cursor(site):
    site.commit("run-1", text="The user prefers Chinese.")
    cursor = _cursor(site)

    result = await DreamConsolidator(site.service).consolidate(
        _runtime(LLMResponse(content="not json")),
    )

    assert result.status == "failed"
    assert _cursor(site) == cursor
    assert site.service.stats()["unconsolidated_facts"] == 1

    retry = await DreamConsolidator(site.service).consolidate(_runtime(_proposal()))
    assert retry.status == "completed"
    assert _cursor(site) == str(site.service.store.max_fact_rowid())


async def test_concurrent_consolidation_is_serialized(site):
    site.commit("run-1", text="The user prefers Chinese.")

    class BlockingProvider(ScriptedProvider):
        def __init__(self, *responses):
            super().__init__(*responses)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def chat(self, messages, **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().chat(messages, **kwargs)

    provider = BlockingProvider(_proposal())
    runtime = _runtime_from(provider)
    first = asyncio.create_task(DreamConsolidator(site.service).consolidate(runtime))
    await provider.entered.wait()

    second = asyncio.create_task(DreamConsolidator(site.service).consolidate(runtime))
    await asyncio.sleep(0.01)
    assert not second.done()
    provider.release.set()

    completed, idle = await asyncio.gather(first, second)
    assert completed.status == "completed"
    assert idle.status == "idle"
    assert len(site.service.store.dream_versions()) == 1


async def test_an_unfinished_claim_from_another_process_reports_busy(site):
    site.commit("run-1", text="The user prefers Chinese.")
    store = site.service.store
    run_id = store.claim_dream_run(cursor_from=0, cursor_to=store.max_fact_rowid())
    assert run_id is not None

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal()))

    assert result.status == "busy"
    assert store.dream_run(run_id)["status"] == "planned"
    assert _cursor(site) is None


def _runtime_from(provider: ScriptedProvider) -> LLMRuntime:
    return LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)


async def test_recovery_after_database_apply_does_not_apply_twice(site):
    site.record("run-1", ("user", "I moved to Berlin."))
    evidence_id = site.evidence_id("run-1")
    site.commit("run-2", subject="user", predicate="editor", text="The user uses Neovim.")
    store = site.service.store
    run_id = store.claim_dream_run(cursor_from=0, cursor_to=store.max_fact_rowid())
    assert run_id is not None
    proposal = {"operations": [{
        "op": "add_fact", "subject": "user", "predicate": "city",
        "text": "The user lives in Berlin.", "kind": "user_fact",
        "scope": "global", "evidence": [evidence_id],
    }]}
    store.update_dream_run(run_id, proposal=proposal)
    operations = validate_operations(proposal, store, site.service.core)
    store.apply_dream_operations(run_id, operations, changes=len(operations))
    assert len(store.recent_by_status(FactStatus.ACTIVE, 10)) == 2

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal()))

    assert result.status == "completed"
    assert len(store.recent_by_status(FactStatus.ACTIVE, 10)) == 2
    assert store.dream_run(run_id)["status"] == "completed"
    assert _cursor(site) == str(store.max_fact_rowid())


async def test_recovery_after_files_written_finishes_the_commit(site):
    site.commit("run-1", text="The user prefers Chinese.")
    store = site.service.store
    run_id = store.claim_dream_run(cursor_from=0, cursor_to=store.max_fact_rowid())
    assert run_id is not None
    site.service.core.write(USER_PATH, "- The user prefers Chinese.\n")
    store.update_dream_run(
        run_id,
        status="files_written",
        proposal=_proposal_dict(),
        applied={"changes": 1, "files": {USER_PATH: site.service.core.hash(USER_PATH)}},
    )

    await DreamConsolidator(site.service).consolidate(_runtime(_proposal()))

    record = store.dream_run(run_id)
    assert record["status"] == "completed"
    assert record["git_commit"]
    assert _cursor(site) == str(store.max_fact_rowid())


async def test_recovery_after_git_commit_does_not_create_another_version(site):
    site.commit("run-1", text="The user prefers Chinese.")
    store = site.service.store
    run_id = store.claim_dream_run(cursor_from=0, cursor_to=store.max_fact_rowid())
    assert run_id is not None
    site.service.core.write(USER_PATH, "- The user prefers Chinese.\n")
    commit = site.service.git.auto_commit("dream: consolidate memory (1 change(s))")
    assert commit is not None
    store.update_dream_run(
        run_id,
        status="git_committed",
        git_commit=commit,
        proposal=_proposal_dict(),
        applied={"changes": 1, "files": {USER_PATH: site.service.core.hash(USER_PATH)}},
    )
    before = len(site.service.git.log())

    await DreamConsolidator(site.service).consolidate(_runtime(_proposal()))

    assert store.dream_run(run_id)["status"] == "completed"
    assert len(site.service.git.log()) == before
    assert _cursor(site) == str(store.max_fact_rowid())


async def test_restore_reverts_files_and_fact_transitions(site):
    site.record("run-1", ("user", "I moved to Berlin."))
    evidence_id = site.evidence_id("run-1")
    site.commit("run-2", subject="user", predicate="editor", text="The user uses Neovim.")

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal(
        {
            "op": "add_fact", "subject": "user", "predicate": "city",
            "text": "The user lives in Berlin.", "kind": "user_fact",
            "scope": "global", "evidence": [evidence_id],
        },
        {"op": "update_core", "file": USER_PATH, "content": "- The user lives in Berlin.\n"},
    )))
    assert result.status == "completed"
    added = next(
        fact for fact in site.service.store.recent_by_status(FactStatus.ACTIVE, 10)
        if fact.predicate == "city"
    )

    restored = site.service.restore_dream(result.run_id)

    assert restored["restored_files"] == [USER_PATH]
    assert site.service.core.read_user() == ""
    assert site.service.store.fact(added.id) is None
    assert site.service.store.dream_run(restored["run_id"])["applied"]["restore_of"] == result.run_id


async def test_restore_refuses_after_newer_core_edits(site):
    site.commit("run-1", text="The user prefers Chinese.")
    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "update_core", "file": USER_PATH, "content": "- consolidated\n",
    })))
    site.service.core.write(USER_PATH, "- edited by someone else\n")

    with pytest.raises(DreamError, match="changed after this consolidation"):
        site.service.restore_dream(result.run_id)


async def test_restore_requires_a_completed_consolidation(site):
    store = site.service.store
    run_id = store.create_dream_run(cursor_from=0, cursor_to=0)

    with pytest.raises(DreamError, match="not completed"):
        site.service.restore_dream(run_id)
    with pytest.raises(DreamError, match="unknown consolidation"):
        site.service.restore_dream("dream_missing")


async def test_restore_never_resurrects_forgotten_facts(site):
    site.commit("run-1", text="The user prefers Chinese.")
    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "update_core", "file": USER_PATH, "content": "- consolidated\n",
    })))
    fact = site.service.store.recent_by_status(FactStatus.ACTIVE, 1)[0]
    site.service.store.delete_fact(fact.id)

    restored = site.service.restore_dream(result.run_id)

    assert restored["restored_facts"] == 0
    assert site.service.store.fact(fact.id) is None


async def test_restore_of_a_restore_is_refused(site):
    site.commit("run-1", text="The user prefers Chinese.")
    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "update_core", "file": USER_PATH, "content": "- consolidated\n",
    })))
    audit = site.service.restore_dream(result.run_id)

    with pytest.raises(DreamError, match="cannot itself be restored"):
        site.service.restore_dream(audit["run_id"])


def _proposal_dict() -> dict:
    return {"operations": []}


async def test_dream_can_read_full_evidence_with_its_read_only_tools(site):
    site.record("run-1", ("user", "I switched to Neovim last week."))
    evidence_id = site.evidence_id("run-1")
    fact_id = site.service.store.commit_candidates([site.draft(
        subject="user", predicate="editor", text="The user uses Neovim.",
        sources=(evidence_id,),
    )])[0]
    fact = site.service.store.fact(fact_id)
    assert fact is not None
    from memory_helpers import ScriptedProvider

    from yoreni.providers.base import ToolCallRequest

    def request_evidence(messages):
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(
                id="call-1", name="memory_get", arguments={"fact_id": fact.id},
            )],
            finish_reason="tool_calls",
        )

    def finish(messages):
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        assert tool_messages and evidence_id in tool_messages[0]["content"]
        return LLMResponse(content='{"operations": []}')

    runtime = LLMRuntime.capture(
        ScriptedProvider(request_evidence, finish),
        "test-model",
        context_window_tokens=128_000,
    )
    result = await DreamConsolidator(site.service).consolidate(runtime)

    assert result.status == "completed"
    assert result.changes == 0


async def test_dream_tool_lists_pending_skill_proposals(site):
    from yoreni.agent.tools.dream import DreamTool

    site.commit("run-1", text="The user always runs tests before committing.")
    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "propose_skill",
        "name": "test-first",
        "description": "Run tests before committing.",
        "body": "Run pytest, then commit.",
    })))
    assert result.status == "completed"

    payload = json.loads(await DreamTool(site.service, None).execute(action="proposals"))
    assert payload["proposals"][0]["name"] == "test-first"
    assert payload["proposals"][0]["status"] == "pending"


async def test_dream_ends_with_a_change_set_when_its_tool_rounds_run_out(site):
    site.commit("run-1", text="The user prefers Chinese.")

    def search(messages):
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(
                id=f"call-{len(messages)}", name="memory_search",
                arguments={"query": "user"},
            )],
            finish_reason="tool_calls",
        )

    provider = ScriptedProvider(*[search] * (MAX_TOOL_ROUNDS - 1), _proposal())
    result = await DreamConsolidator(site.service).consolidate(_runtime_from(provider))

    assert result.status == "completed"
    assert len(provider.calls) == MAX_TOOL_ROUNDS
    # The last round has no tools left, so it cannot consume the budget again.
    assert provider.options[-1]["tools"] is None
    assert provider.options[0]["tools"] is not None


async def test_dream_repairs_one_non_json_reply(site):
    site.commit("run-1", text="The user prefers Chinese.")
    provider = ScriptedProvider(
        LLMResponse(content="I will consolidate the memory now."),
        _proposal(),
    )

    result = await DreamConsolidator(site.service).consolidate(_runtime_from(provider))

    assert result.status == "completed"
    assert len(provider.calls) == 2
    assert provider.calls[-1][-1]["role"] == "user"


async def test_a_failed_dream_tool_does_not_abort_the_pass(site):
    from yoreni.memory.embeddings import EmbeddingError

    site.commit("run-1", text="The user prefers Chinese.")

    class UnreachableEmbedder:
        settings = type("Settings", (), {"model": "unreachable", "dimensions": 2})()

        async def embed(self, texts):
            raise EmbeddingError("embedding request failed: ConnectError")

        async def embed_query(self, text):
            raise EmbeddingError("embedding request failed: ConnectError")

    site.service.embedder = UnreachableEmbedder()
    site.service.retriever.embedder = site.service.embedder
    provider = ScriptedProvider(
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(
                id="call-1", name="memory_search", arguments={"query": "language"},
            )],
            finish_reason="tool_calls",
        ),
        _proposal(),
    )

    result = await DreamConsolidator(site.service).consolidate(_runtime_from(provider))

    assert result.status == "completed"
    tool_messages = [item for item in provider.calls[-1] if item.get("role") == "tool"]
    assert tool_messages[0]["content"].startswith("Error:")


async def test_a_failed_pass_still_records_its_token_cost(site):
    from yoreni.llm_usage.types import LLMUsage

    site.commit("run-1", text="The user prefers Chinese.")
    provider = ScriptedProvider(LLMResponse(
        content=None,
        usage=LLMUsage(
            input_tokens=120, output_tokens=30, total_tokens=150, reported_tokens=150,
        ),
    ))

    result = await DreamConsolidator(site.service).consolidate(_runtime_from(provider))

    assert result.status == "failed"
    record = site.service.store.dream_run(result.run_id)
    assert (record["tokens_in"], record["tokens_out"]) == (120, 30)


async def test_dream_cannot_resolve_a_disputed_fact(site):
    site.record("run-1", ("user", "I prefer Chinese."))
    evidence = site.evidence_id("run-1")
    site.service.store.commit_candidates([site.draft(
        text="The user prefers Chinese.", sources=(evidence,),
    )])
    disputed = site.service.store.commit_candidates([site.draft(
        text="The user prefers French.", sources=(evidence,),
        source_type="agent_inference", observed_at="2027-01-01T00:00:00+00:00",
    )])[0]
    assert site.service.store.fact(disputed).status is FactStatus.DISPUTED

    result = await DreamConsolidator(site.service).consolidate(_runtime(_proposal({
        "op": "set_status", "fact_id": disputed, "status": "active",
    })))

    assert result.status == "failed"
    assert "cannot change the status" in result.detail
    assert site.service.store.fact(disputed).status is FactStatus.DISPUTED
