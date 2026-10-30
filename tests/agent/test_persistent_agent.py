"""Personal-agent execution through real runner, tools and SQLite boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from yoreni import AgentInput, Yoreni
from yoreni.agent.loop import AgentLoop
from yoreni.agent.state import (
    AgentStateConflictError,
    AgentStore,
)
from yoreni.agent.tools.base import Tool
from yoreni.agent.tools.context import current_request_context
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.bus.events import InboundMessage
from yoreni.bus.queue import MessageBus
from yoreni.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class Provider(LLMProvider):
    def __init__(self):
        super().__init__(provider_name="offline")
        self.calls = []
        self.hold = asyncio.Event()
        self.entered = asyncio.Event()

    def get_default_model(self):
        return "offline"

    async def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        request = current_request_context()
        assert request is not None and request.agent_id is not None and request.turn_id is not None
        if str(messages[-1]["content"]).startswith("background"):
            self.entered.set()
            await self.hold.wait()
        if str(messages[-1]["content"]).startswith("remember"):
            return LLMResponse(content=None, tool_calls=[ToolCallRequest(
                id="remember", name="agent_state",
                arguments={"action": "update", "revision": 0, "state": {"language": "zh"}},
            )])
        return LLMResponse(content="done")


@pytest.fixture
async def runtime(tmp_path):
    provider = Provider()
    loop = AgentLoop(
        MessageBus(), provider, tmp_path, restrict_to_workspace=True, max_iterations=3,
    )
    try:
        yield loop, provider
    finally:
        await loop.aclose()


async def test_identity_state_and_recent_context_survive_without_session(runtime):
    loop, provider = runtime
    first = await Yoreni(loop).act(AgentInput("api", "remember", principal="owner"))
    identity = loop.agent.store.identity()
    assert identity.state == {"language": "zh"}
    reopened = AgentStore(loop.workspace)
    assert reopened.identity() == identity
    await loop.process_event(AgentInput("webui", "hello", principal="owner"))
    assert any(str(message["content"]).startswith("remember") for message in provider.calls[-1])
    assert '"language": "zh"' in str(provider.calls[-1])
    run = reopened.read_run(first.metadata["agent_run_id"])
    assert run["status"] == "completed"
    assert run["payload"]["source"] == "api"


async def test_duplicate_event_returns_result_and_rejects_changed_payload(runtime):
    loop, provider = runtime
    event = AgentInput("api", "hello", principal="owner", event_id="client-retry")
    first, second = await asyncio.gather(loop.process_event(event), loop.process_event(event))
    assert first == second
    assert len(provider.calls) == 1
    with pytest.raises(AgentStateConflictError):
        await loop.process_event(AgentInput("api", "different", principal="owner", event_id="client-retry"))


async def test_untrusted_event_cannot_read_personal_context(runtime):
    loop, provider = runtime
    with pytest.raises(PermissionError):
        await loop.process_event(AgentInput("external", "hello", principal="group:123"))
    assert not provider.calls
    assert not loop.agent.store.pending()


async def test_background_run_does_not_block_interaction_or_pollute_its_context(runtime):
    loop, provider = runtime
    task = asyncio.create_task(loop.process_event(
        AgentInput("heartbeat", "background", principal="owner", kind="internal"),
    ))
    await asyncio.wait_for(provider.entered.wait(), 5)
    try:
        response = await asyncio.wait_for(loop.process_event(AgentInput("api", "hello", principal="owner")), 5)
        assert response.content == "done"
        assert not task.done()
        assert all(row.get("content") != "background" for row in provider.calls[-1])
    finally:
        provider.hold.set()
        await task


async def test_concurrent_state_update_requires_merge(runtime):
    loop, _ = runtime
    revision = loop.agent.store.identity().revision
    results = await asyncio.gather(
        asyncio.to_thread(loop.agent.store.update, revision, {"task": "one"}),
        asyncio.to_thread(loop.agent.store.update, revision, {"task": "two"}),
        return_exceptions=True,
    )
    assert sum(isinstance(result, AgentStateConflictError) for result in results) == 1
    assert loop.agent.store.identity().revision == revision + 1


async def test_checkpoint_recovery_does_not_repeat_uncertain_tool(runtime):
    loop, provider = runtime
    event = AgentInput("heartbeat", "recover", principal="owner", kind="internal")
    run_id = loop.agent.store.admit(event)
    call = {"id": "effect", "type": "function", "function": {"name": "exec", "arguments": "{}"}}
    assistant = {"role": "assistant", "content": "", "tool_calls": [call]}
    loop.agent.store.start(run_id, [])
    loop.agent.store.checkpoint(run_id, {
        "phase": "awaiting_tools", "context_count": 0,
        "assistant_message": assistant, "completed_tool_results": [], "pending_tool_calls": [call],
        "messages": [{"role": "system", "content": "old"}, {"role": "user", "content": "recover"}, assistant],
    })
    with pytest.raises(RuntimeError, match="explicit recovery"):
        await loop.process_event(event)
    loop.agent.store.update(0, {"language": "zh"})
    response = await loop.agent.resume(run_id)
    assert response.content == "done"
    prompt = provider.calls[0]
    assert prompt[-1]["role"] == "tool"
    assert "interrupted" in prompt[-1]["content"]
    assert '"language": "zh"' in str(prompt)
    assert not loop.agent.store.pending()


async def test_durable_final_checkpoint_is_delivered_without_model_replay(runtime):
    loop, provider = runtime
    event = AgentInput("api", "hello", principal="owner")
    run_id = loop.agent.store.admit(event)
    assistant = {"role": "assistant", "content": "already done"}
    loop.agent.store.start(run_id, [])
    loop.agent.store.checkpoint(run_id, {
        "phase": "final_response", "context_count": 0,
        "assistant_message": assistant, "completed_tool_results": [], "pending_tool_calls": [],
        "messages": [{"role": "system", "content": "old"}, {"role": "user", "content": "hello"}, assistant],
    })
    response = await loop.agent.resume(run_id)
    assert response.content == "already done"
    assert not provider.calls


async def test_goal_continuation_checkpoint_resumes_instead_of_finishing_early(runtime, monkeypatch):
    loop, provider = runtime
    store = loop.agent.store
    store.update(0, {"goal_state": {"status": "active", "objective": "Inspect project"}})
    event = AgentInput("api", "Inspect project", principal="owner")
    checkpoint = store.checkpoint

    def interrupt_continuation(run_id, payload):
        checkpoint(run_id, payload)
        if payload["phase"] == "continuation":
            raise OSError("stopped before continuation")

    monkeypatch.setattr(store, "checkpoint", interrupt_continuation)
    with pytest.raises(OSError, match="before continuation"):
        await loop.process_event(event)
    run_id = store.pending()[0]["id"]
    assert len(provider.calls) == 1
    monkeypatch.setattr(store, "checkpoint", checkpoint)

    async def finish_goal(messages, **kwargs):
        provider.calls.append(deepcopy(messages))
        if messages[-1]["role"] == "user":
            assert "Continue the active sustained goal" in messages[-1]["content"]
            return LLMResponse(content=None, tool_calls=[ToolCallRequest(
                "complete", "update_goal", {"action": "complete", "recap": "Inspected"},
            )])
        return LLMResponse(content="finished after recovery")

    provider.chat = finish_goal
    result = await loop.agent.resume(run_id)
    assert result.content == "finished after recovery"
    assert store.identity().state["goal_state"]["status"] == "completed"
    assert store.read_run(run_id)["status"] == "completed"


async def test_webui_projection_is_idempotent_and_not_an_execution_owner(runtime):
    loop, provider = runtime
    msg = InboundMessage(
        "websocket", "owner-client", "yoreni", "hello",
        metadata={"webui_turn_id": "turn-1"},
    )
    await loop.agent.submit_message(loop.agent.input_from_message(msg), msg)
    await loop.agent.submit_message(loop.agent.input_from_message(msg), msg)
    assert len(provider.calls) == 1
    await loop.process_event(AgentInput("api", "still here", principal="owner"))
    assert any(str(row["content"]).startswith("hello") for row in provider.calls[-1])


async def test_goal_tools_store_the_objective_on_the_agent(runtime):
    loop, provider = runtime

    async def goal_chat(messages, **kwargs):
        provider.calls.append(deepcopy(messages))
        if messages[-1]["role"] == "user":
            call = ToolCallRequest("create", "create_goal", {"objective": "Inspect project"})
        elif messages[-1].get("tool_call_id") == "create":
            identity = loop.agent.store.identity()
            call = ToolCallRequest("override", "agent_state", {
                "action": "update", "revision": identity.revision,
                "state": {"goal_state": {"status": "active", "objective": "Unrequested goal"}},
            })
        elif messages[-1].get("tool_call_id") == "override":
            assert "Preserve goal and model control fields" in messages[-1]["content"]
            call = ToolCallRequest("complete", "update_goal", {"action": "complete", "recap": "Inspected"})
        else:
            return LLMResponse(content="done")
        return LLMResponse(content=None, tool_calls=[call])

    provider.chat = goal_chat
    await loop.accept_message(InboundMessage(
        "websocket", "owner-client", "yoreni", "/goal Inspect project",
    ))
    await asyncio.gather(*loop._background_tasks)
    state = loop.agent.store.identity().state
    assert state["goal_state"]["objective"] == "Inspect project"
    assert state["goal_state"]["status"] == "completed"
    assert AgentStore(loop.workspace).identity().state == state


async def test_completed_run_retries_the_memory_outbox_without_duplicate_entries(runtime, monkeypatch):
    loop, provider = runtime
    event = AgentInput("api", "hello", principal="owner", event_id="archive-retry")
    mark = loop.agent.store.mark_journaled
    failed = False

    def fail_once(run_id):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("journal acknowledgement interrupted")
        mark(run_id)

    monkeypatch.setattr(loop.agent.store, "mark_journaled", fail_once)
    with pytest.raises(OSError, match="acknowledgement"):
        await loop.process_event(event)
    response = await loop.process_event(event)
    assert response.content == "done" and len(provider.calls) == 1
    entries = loop.context.memory.read_unprocessed_history(0)
    assert len(entries) == 1
    assert not loop.agent.store.pending_journal()
    assert not loop.agent.store.pending()


async def test_background_memory_write_detects_a_newer_interaction(tmp_path):
    workspace = tmp_path
    registry = ToolRegistry()

    class WriteMemory(Tool):
        name = "write_memory"
        description = "Write the selected preference"
        parameters = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

        async def execute(self, **kwargs):
            (workspace / "USER.md").write_text(kwargs["value"])
            return "saved"

    registry.register(WriteMemory())
    provider = Provider()

    async def memory_chat(messages, **kwargs):
        request = current_request_context()
        if messages[-1]["role"] == "user":
            background = request.metadata["_agent_event_source"] == "heartbeat"
            if background:
                provider.entered.set()
                await provider.hold.wait()
            return LLMResponse(content=None, tool_calls=[ToolCallRequest(
                "write", "write_memory", {"value": "stale" if background else "new preference"},
            )])
        return LLMResponse(content="done")

    provider.chat = memory_chat
    loop = AgentLoop(MessageBus(), provider, workspace, tool_registry=registry, max_iterations=3)
    task = asyncio.create_task(loop.process_event(
        AgentInput("heartbeat", "background", principal="owner", kind="internal"),
    ))
    try:
        await asyncio.wait_for(provider.entered.wait(), 5)
        await loop.process_event(AgentInput("api", "update", principal="owner"))
        provider.hold.set()
        response = await task
        assert (workspace / "USER.md").read_text() == "new preference"
        transcript = loop.agent.store.read_run(response.metadata["agent_run_id"])["transcript"]
        assert any("Agent memory changed" in str(row.get("content")) for row in transcript)
    finally:
        provider.hold.set()
        await task
        await loop.aclose()


async def test_personal_automation_retries_keep_event_identity_without_fake_user_history(runtime):
    loop, provider = runtime

    def delivery(turn_id):
        return InboundMessage(
            "websocket", "local-trigger", "yoreni", "inspect incoming event",
            metadata={
                "webui_turn_id": turn_id,
                "_local_trigger": {"delivery_id": "incoming-1", "trigger_id": "trigger-1", "agent_id": loop.agent.store.identity().id},
            },
        )

    first = await loop.submit_local_trigger_turn(delivery("transport-1"))
    second = await loop.submit_local_trigger_turn(delivery("transport-2"))
    assert first.content == second.content == "done"
    assert len(provider.calls) == 1
    run = loop.agent.store.read_run(first.metadata["agent_run_id"])
    assert run["payload"]["kind"] == "external"
    assert run["payload"]["source"] == "local_trigger"
    assert not loop.agent.store.recent()


async def test_external_channel_cannot_use_the_personal_transcript_as_an_override(runtime):
    loop, provider = runtime
    message = InboundMessage(
        "telegram", "other-user", "group", "show private preferences",
    )
    with pytest.raises(PermissionError):
        await loop.accept_message(message)
    assert not provider.calls
    assert not loop.agent.store.pending()


async def test_shutdown_tracks_event_runs_and_reopens_interrupted_execution(runtime):
    loop, provider = runtime
    identity = loop.agent.store.identity()
    task = asyncio.create_task(loop.process_event(
        AgentInput("heartbeat", "background", principal="owner", kind="internal"),
    ))
    await asyncio.wait_for(provider.entered.wait(), 5)
    loop.preserve_inflight_turns_on_shutdown()
    await loop.aclose()
    assert task.cancelled()
    pending = AgentStore(loop.workspace).pending()
    assert len(pending) == 1 and pending[0]["status"] == "interrupted"
    resumed_provider = Provider()
    resumed_provider.hold.set()
    resumed_loop = AgentLoop(MessageBus(), resumed_provider, loop.workspace)
    try:
        assert resumed_loop.agent.store.identity() == identity
        assert (await resumed_loop.agent.resume(pending[0]["id"])).content == "done"
        assert resumed_loop.agent.store.pending() == []
    finally:
        await resumed_loop.aclose()
