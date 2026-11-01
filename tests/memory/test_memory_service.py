"""Service surface: context blocks, explicit writes, forgetting, core memory, tools."""

from __future__ import annotations

import json

import pytest
from memory_helpers import MemoryHarness

from yoreni.agent.tools.context import RequestContext, ToolContext
from yoreni.agent.tools.memory import (
    MemoryCorrectTool,
    MemoryForgetTool,
    MemoryGetTool,
    MemoryRememberTool,
    MemorySearchTool,
)
from yoreni.config.schema import MemoryConfig, ToolsConfig
from yoreni.memory.core import MEMORY_PATH, SOUL_PATH, USER_PATH, CoreMemory
from yoreni.memory.models import MemoryError, MemorySchemaError
from yoreni.memory.service import MemoryService


async def test_context_block_marks_retrieved_memory_as_data(harness):
    fact_id = harness.commit("run-1", text="The user prefers Chinese.")

    block = await harness.service.context_block("Chinese")

    assert block is not None
    assert block.source == "memory"
    assert "not instructions" in block.content
    assert f"[fact:{fact_id}]" in block.content
    assert "user_statement" in block.content
    assert "evidence" in block.content
    assert "observed" in block.content


async def test_context_block_is_absent_when_nothing_is_relevant(harness):
    assert await harness.service.context_block("   ") is None
    assert await harness.service.context_block("quantum chromodynamics") is None


async def test_context_block_reports_retrieval_failure_instead_of_empty_memory(harness):
    class BrokenEmbedder:
        settings = type("Settings", (), {"model": "broken", "dimensions": 2})()

        async def embed_query(self, text):
            raise RuntimeError("embedding endpoint down")

        async def embed(self, texts):
            raise RuntimeError("embedding endpoint down")

    harness.service.retriever.embedder = BrokenEmbedder()
    block = await harness.service.context_block("anything")

    assert block is not None
    assert "unavailable" in block.content
    assert "Do not claim to remember" in block.content


async def test_context_block_respects_the_retrieval_token_budget(tmp_path):
    config = MemoryConfig(retrieval_token_budget=200)
    service = MemoryService(tmp_path, config)
    harness = MemoryHarness(service, tmp_path)
    for index in range(12):
        harness.commit(
            f"run-{index}",
            subject="user",
            predicate=f"detail_{index}",
            text=("A long remembered detail " * 40) + str(index),
            kind="event_experience",
        )

    block = await service.context_block("long remembered detail")
    assert block is not None
    # 12 untrimmed hits would be far larger than the 200-token allowance.
    assert len(block.content) < 4_000


async def test_remember_requires_a_user_message_in_the_run(harness):
    harness.record("run-1", ("assistant", "I will remember that."))

    with pytest.raises(MemoryError, match="no durable user message"):
        await harness.service.remember(
            run_id="run-1", subject="user", predicate="name",
            text="The user is Ada.", kind="user_fact",
        )


async def test_remember_uses_the_run_message_as_evidence(harness):
    harness.record("run-1", ("user", "My name is Ada."))

    [fact_id] = await harness.service.remember(
        run_id="run-1", subject="user", predicate="name",
        text="The user's name is Ada.", kind="user_fact",
    )

    detail = harness.service.fact_detail(fact_id)
    assert detail is not None
    assert detail["fact"]["source_type"] == "user_statement"
    assert detail["sources"][0]["quote"] == "My name is Ada."
    assert detail["sources"][0]["run_id"] == "run-1"


async def test_correct_keeps_the_previous_version(harness):
    old_id = harness.commit("run-1", text="The user prefers Chinese.")
    harness.record("run-2", ("user", "Actually I prefer English."))

    new_id = await harness.service.correct(
        run_id="run-2", fact_id=old_id, text="The user prefers English.",
    )

    old = harness.service.store.fact(old_id)
    new = harness.service.store.fact(new_id)
    assert old is not None and new is not None
    assert old.status.value == "superseded"
    assert new.status.value == "active"
    assert new.supersedes == old_id
    assert new.source_type.value == "user_correction"


async def test_correct_rejects_unknown_facts(harness):
    harness.record("run-1", ("user", "hello"))
    with pytest.raises(MemoryError, match="unknown fact"):
        await harness.service.correct(run_id="run-1", fact_id="fact_missing", text="x")


async def test_forget_deletes_the_fact_and_scrubs_core_summaries(harness):
    fact_id = harness.commit("run-1", text="The user prefers Chinese.")
    harness.workspace.joinpath(USER_PATH).write_text(
        "- The user prefers Chinese.\n- The user lives in Berlin.\n", encoding="utf-8",
    )
    harness.workspace.joinpath(MEMORY_PATH).parent.mkdir(exist_ok=True)
    harness.workspace.joinpath(MEMORY_PATH).write_text(
        "Background notes.\nThe user prefers Chinese.\n", encoding="utf-8",
    )

    result = await harness.service.forget(run_id="run-1", fact_id=fact_id)

    assert result["deleted"] == [fact_id]
    assert set(result["core_files"]) == {USER_PATH, MEMORY_PATH}
    assert harness.service.store.fact(fact_id) is None
    assert "Chinese" not in harness.workspace.joinpath(USER_PATH).read_text(encoding="utf-8")
    assert "Berlin" in harness.workspace.joinpath(USER_PATH).read_text(encoding="utf-8")
    assert "Chinese" not in harness.workspace.joinpath(MEMORY_PATH).read_text(encoding="utf-8")
    assert "Git audit history" in result["git_history_note"]
    assert "agent.sqlite3" in result["source_note"]
    assert "memory/backups/" in result["backups_note"]


async def test_forget_by_query_deletes_matching_facts(harness):
    first = harness.commit("run-1", text="The user prefers Chinese.")
    second = harness.commit("run-2", subject="user", predicate="city",
                            text="The user lives in Berlin.")

    result = await harness.service.forget(run_id="run-2", query="Chinese")

    assert result["deleted"] == [first]
    assert harness.service.store.fact(first) is None
    assert harness.service.store.fact(second) is not None


async def test_forget_requires_a_target(harness):
    with pytest.raises(MemoryError, match="requires a fact_id or a query"):
        await harness.service.forget(run_id="run-1")


async def test_core_memory_writes_identity_and_refuses_other_files(harness):
    core = CoreMemory(harness.workspace)
    core.write(SOUL_PATH, "new identity")
    assert core.read_soul() == "new identity"
    with pytest.raises(PermissionError, match="not maintained"):
        core.write("AGENTS.md", "not a core file")


async def test_core_apply_rejects_a_stale_snapshot(harness):
    core = CoreMemory(harness.workspace)
    core.write(USER_PATH, "initial")
    snapshot = core.snapshot()
    harness.workspace.joinpath(USER_PATH).write_text("edited elsewhere", encoding="utf-8")

    with pytest.raises(MemorySchemaError, match="changed since"):
        core.apply({USER_PATH: "from dream"}, expected=snapshot)


def test_core_sections_stay_inside_the_budget(tmp_path):
    core = CoreMemory(tmp_path, token_budget=600)
    (tmp_path / SOUL_PATH).write_text("identity " * 2_000, encoding="utf-8")
    (tmp_path / USER_PATH).write_text("profile " * 2_000, encoding="utf-8")
    (tmp_path / MEMORY_PATH).parent.mkdir()
    (tmp_path / MEMORY_PATH).write_text("background " * 2_000, encoding="utf-8")

    sections = core.sections()
    assert [name for name, _content in sections] == [SOUL_PATH, USER_PATH, MEMORY_PATH]
    assert all(content for _name, content in sections)
    assert all(len(content) < 14_000 for _name, content in sections)


def test_stats_expose_backlog_and_consolidation_state(harness):
    harness.commit("run-1", text="The user prefers Chinese.")
    stats = harness.service.stats()

    assert stats["active_facts"] == 1
    assert stats["evidence_items"] == 1
    assert stats["unconsolidated_facts"] == 1
    assert stats["last_dream_status"] is None


def _tool_context(service: MemoryService, workspace) -> ToolContext:
    return ToolContext(config=ToolsConfig(), workspace=str(workspace), memory=service)


def _request(run_id: str) -> RequestContext:
    return RequestContext(channel="api", chat_id="test", turn_id=run_id)


def test_memory_tools_are_hidden_when_memory_is_disabled():
    ctx = ToolContext(config=ToolsConfig(), workspace=".", memory=None)
    assert MemorySearchTool.enabled(ctx) is False
    assert MemoryRememberTool.enabled(ctx) is False


async def test_memory_search_and_get_tools_return_structured_results(harness):
    fact_id = harness.commit(
        "run-1", text="The user prefers Chinese.", evidence_text="I prefer Chinese.",
    )
    ctx = _tool_context(harness.service, harness.workspace)

    search = MemorySearchTool.create(ctx)
    search.set_context(_request("run-1"))
    payload = json.loads(await search.execute(query="Chinese"))

    assert payload["facts"][0]["fact_id"] == fact_id
    assert payload["facts"][0]["status"] == "active"
    assert payload["facts"][0]["sources"][0]["run_id"] == "run-1"
    assert payload["episodes"][0]["run_id"] == "run-1"
    assert payload["semantic_index"] is False

    detail = MemoryGetTool.create(ctx)
    fact = json.loads(await detail.execute(fact_id=fact_id))
    assert fact["fact"]["id"] == fact_id
    assert fact["versions"][0]["id"] == fact_id

    missing = await detail.execute(fact_id="fact_missing")
    assert missing.is_error is True and "unknown fact" in missing


async def test_memory_write_tools_require_an_active_run(harness):
    ctx = _tool_context(harness.service, harness.workspace)
    remember = MemoryRememberTool.create(ctx)
    remember.set_context(RequestContext(channel="api", chat_id="test"))

    result = await remember.execute(
        subject="user", predicate="name", text="The user is Ada.", kind="user_fact",
    )
    assert result.is_error is True and "active Run" in result


async def test_memory_remember_correct_and_forget_tools(harness):
    ctx = _tool_context(harness.service, harness.workspace)
    remember = MemoryRememberTool.create(ctx)
    remember.set_context(_request("run-1"))
    harness.record("run-1", ("user", "Remember that I prefer Chinese."))

    stored = json.loads(await remember.execute(
        subject="user", predicate="preferred_language",
        text="The user prefers Chinese.", kind="user_preference",
    ))
    [fact_id] = stored["stored"]

    harness.record("run-2", ("user", "Actually I prefer English."))
    correct = MemoryCorrectTool.create(ctx)
    correct.set_context(_request("run-2"))
    corrected = json.loads(await correct.execute(
        fact_id=fact_id, text="The user prefers English.",
    ))
    assert corrected["supersedes"] == fact_id
    assert corrected["fact_id"] != fact_id

    forget = MemoryForgetTool.create(ctx)
    forget.set_context(_request("run-3"))
    removed = json.loads(await forget.execute(fact_id=corrected["fact_id"]))
    assert set(removed["deleted"]) == {fact_id, corrected["fact_id"]}
    assert harness.service.store.fact(corrected["fact_id"]) is None
    assert harness.service.store.fact(fact_id) is None


async def test_world_fact_cannot_cite_only_the_users_request(harness):
    harness.record("run-1", ("user", "Please look up the latest version."))
    user_evidence = harness.evidence_id("run-1", "user")

    with pytest.raises(MemoryError, match="does not mention"):
        await harness.service.remember(
            run_id="run-1",
            subject="project:yoreni",
            predicate="version",
            text="Yoreni is on version 2.4.1.",
            kind="project_knowledge",
            evidence_ids=[user_evidence],
        )


async def test_world_fact_can_cite_the_tool_result_that_established_it(harness):
    harness.record(
        "run-1",
        ("user", "Please look up the latest version."),
        ("tool", "Yoreni version 2.4.1 is the latest release."),
    )
    tool_evidence = harness.evidence_id("run-1", "tool")

    [fact_id] = await harness.service.remember(
        run_id="run-1",
        subject="project:yoreni",
        predicate="version",
        text="Yoreni is on version 2.4.1.",
        kind="project_knowledge",
        evidence_ids=[tool_evidence],
    )

    detail = harness.service.fact_detail(fact_id)
    assert detail["fact"]["source_type"] == "tool_result"
    assert detail["sources"][0]["evidence_id"] == tool_evidence
    assert detail["sources"][0]["run_id"] == "run-1"


async def test_evidence_from_another_run_is_rejected(harness):
    harness.record("run-1", ("tool", "Yoreni version 2.4.1 is the latest release."))
    other_evidence = harness.evidence_id("run-1", "tool")
    harness.record("run-2", ("user", "A different request."))

    with pytest.raises(MemoryError, match="another Run"):
        await harness.service.remember(
            run_id="run-2",
            subject="project:yoreni",
            predicate="version",
            text="Yoreni is on version 2.4.1.",
            kind="project_knowledge",
            evidence_ids=[other_evidence],
        )


async def test_a_cited_tool_result_must_mention_the_fact(harness):
    harness.record(
        "run-1",
        ("user", "Look it up."),
        ("tool", "The weather in Berlin is sunny."),
    )
    tool_evidence = harness.evidence_id("run-1", "tool")

    with pytest.raises(MemoryError, match="does not mention"):
        await harness.service.remember(
            run_id="run-1",
            subject="project:yoreni",
            predicate="version",
            text="Yoreni is on version 2.4.1.",
            kind="project_knowledge",
            evidence_ids=[tool_evidence],
        )


def test_tool_evidence_recorded_during_a_run_is_reused_at_completion(harness):
    from yoreni.memory.models import EvidenceKind

    evidence_id = harness.service.record_tool_result(
        "run-1", "exec", "Yoreni version 2.4.1 is the latest release.",
    )
    summary = harness.service.record_run("run-1", [
        {"role": "user", "content": "look up the latest version"},
        {"role": "tool", "name": "exec", "content": "Yoreni version 2.4.1 is the latest release."},
        {"role": "assistant", "content": "It is 2.4.1."},
    ])

    rows = harness.service.store.evidence_for_run("run-1")
    tool_rows = [row for row in rows if row.kind is EvidenceKind.TOOL_RESULT]
    assert [row.id for row in tool_rows] == [evidence_id]
    assert summary.recorded == 3


async def test_memory_tools_expose_current_run_evidence_and_accept_it(harness):
    evidence_id = harness.service.record_tool_result(
        "run-1", "exec", "Yoreni version 2.4.1 is the latest release.",
    )
    ctx = _tool_context(harness.service, harness.workspace)
    search = MemorySearchTool.create(ctx)
    search.set_context(_request("run-1"))

    payload = json.loads(await search.execute(query="version"))
    assert payload["current_run_evidence"][0]["evidence_id"] == evidence_id
    assert payload["current_run_evidence"][0]["tool_name"] == "exec"

    remember = MemoryRememberTool.create(ctx)
    remember.set_context(_request("run-1"))
    stored = json.loads(await remember.execute(
        subject="project:yoreni",
        predicate="version",
        text="Yoreni is on version 2.4.1.",
        kind="project_knowledge",
        evidence=[evidence_id],
    ))

    detail = harness.service.fact_detail(stored["stored"][0])
    assert detail is not None
    assert detail["fact"]["source_type"] == "tool_result"
    assert detail["sources"][0]["evidence_id"] == evidence_id
