"""SDK configuration and Agent/Event/Run execution contracts."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yoreni.agent.hook import AgentHook
from yoreni.agent.tools.base import Tool
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.providers.base import LLMProvider, LLMResponse, LLMUsage, ToolCallRequest
from yoreni.yoreni import (
    STREAM_EVENT_REASONING_COMPLETED,
    STREAM_EVENT_REASONING_DELTA,
    STREAM_EVENT_RUN_COMPLETED,
    STREAM_EVENT_RUN_FAILED,
    STREAM_EVENT_RUN_STARTED,
    STREAM_EVENT_TEXT_COMPLETED,
    STREAM_EVENT_TEXT_DELTA,
    STREAM_EVENT_TOOL_COMPLETED,
    STREAM_EVENT_TOOL_FAILED,
    STREAM_EVENT_TOOL_STARTED,
    STREAM_EVENT_TYPES,
    AgentInput,
    RunResult,
    RunStream,
    Yoreni,
)


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    data = {
        "providers": {"openrouter": {"apiKey": "sk-test-key"}},
        "agents": {"defaults": {"model": "openai/gpt-4.1"}},
    }
    if overrides:
        data.update(overrides)
    config_dir = tmp_path.parent / f"{tmp_path.name}-instance"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(data))
    return config_path


def _fake_provider(name: str, *, max_tokens: int = 8192) -> MagicMock:
    provider = MagicMock(name=name)
    provider.get_default_model.return_value = name
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    return provider


def test_from_config_missing_file():
    with pytest.raises(FileNotFoundError):
        Yoreni.from_config("/nonexistent/config.json")


def test_from_config_missing_env_reports_explicit_config_path(
    tmp_path,
    monkeypatch,
) -> None:
    from yoreni.config.errors import ConfigLoadError

    name = "YORENI_TEST_SDK_MISSING_KEY"
    monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "custom.json"
    config_path.write_text(
        json.dumps({"providers": {"openrouter": {"apiKey": f"${{{name}}}"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError) as exc_info:
        Yoreni.from_config(config_path)

    assert exc_info.value.path == config_path.resolve()


def test_from_config_creates_instance(tmp_path):
    config_path = _write_config(tmp_path)
    workspace = tmp_path / "workspace"
    bot = Yoreni.from_config(config_path, workspace=workspace)
    assert bot._loop is not None
    assert bot._loop.workspace == workspace
    assert bot._loop.agent.store.identity().id


def test_from_config_composes_configured_mcp_outside_agent_loop(tmp_path):
    config_path = _write_config(
        tmp_path,
        {"tools": {"mcpServers": {"demo": {"command": "fake-mcp"}}}},
    )

    bot = Yoreni.from_config(config_path, workspace=tmp_path)

    assert bot._mcp_provider is not None
    assert bot._mcp_provider.configured_server_names == {"demo"}
    assert bot._mcp_provider._registry is bot._loop.tools


@pytest.mark.parametrize("provider_name", ["openrouter", "SenseNova"])
def test_from_config_uses_instance_image_settings(tmp_path, monkeypatch, provider_name):
    from yoreni.agent.tools.image_generation import ImageGenerationTool

    monkeypatch.setattr("yoreni.config.loader._current_config_path", tmp_path / "default.json")
    config_path = _write_config(
        tmp_path,
        {
            "providers": {
                "openrouter": {"apiKey": "sk-test-key"},
                "SenseNova": {"apiKey": "named-image-key", "apiBase": "https://images.test/v1"},
            },
            "tools": {"imageGeneration": {
                "enabled": True, "model": "instance-image-model", "provider": provider_name,
            }},
        },
    )

    bot = Yoreni.from_config(config_path, workspace=tmp_path)

    tool = bot._loop.tools.get("generate_image")
    assert isinstance(tool, ImageGenerationTool)
    assert tool.config.model == "instance-image-model"
    client = tool._provider_client()
    assert client is not None
    assert client.api_key == ("sk-test-key" if provider_name == "openrouter" else "named-image-key")


def test_from_config_accepts_default_model_override(tmp_path):
    config_path = _write_config(tmp_path)

    bot = Yoreni.from_config(
        config_path,
        workspace=tmp_path,
        model="openai/gpt-4.1-mini",
    )

    assert bot.runtime.model == "openai/gpt-4.1-mini"
    assert bot._loop.model_preset is None


def test_from_config_accepts_default_model_preset(tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "modelPresets": {
                "fast": {
                    "model": "openai/gpt-4.1-mini",
                    "provider": "openrouter",
                }
            }
        },
    )

    bot = Yoreni.from_config(config_path, workspace=tmp_path, model_preset="fast")

    assert bot.runtime.model == "openai/gpt-4.1-mini"
    assert bot._loop.model_preset == "fast"


def test_from_config_rejects_multiple_model_selectors(tmp_path):
    config_path = _write_config(tmp_path)

    with pytest.raises(ValueError, match="mutually exclusive"):
        Yoreni.from_config(
            config_path,
            workspace=tmp_path,
            model="openai/gpt-4.1",
            model_preset="fast",
        )


def test_from_config_default_path():
    from yoreni.config.schema import Config

    with patch("yoreni.config.loader.load_config") as mock_load, \
         patch("yoreni.providers.factory.make_provider") as mock_prov:
        mock_load.return_value = Config()
        mock_prov.return_value = MagicMock()
        mock_prov.return_value.get_default_model.return_value = "test"
        mock_prov.return_value.generation.max_tokens = 4096
        Yoreni.from_config()
        mock_load.assert_called_once_with(None)


def test_workspace_override(tmp_path):
    config_path = _write_config(tmp_path)
    custom_ws = tmp_path / "custom_workspace"
    custom_ws.mkdir()

    bot = Yoreni.from_config(config_path, workspace=custom_ws)
    assert bot._loop.workspace == custom_ws


def test_stream_event_constants_are_stable():
    assert STREAM_EVENT_TYPES == (
        STREAM_EVENT_RUN_STARTED,
        STREAM_EVENT_TEXT_DELTA,
        STREAM_EVENT_TEXT_COMPLETED,
        STREAM_EVENT_REASONING_DELTA,
        STREAM_EVENT_REASONING_COMPLETED,
        STREAM_EVENT_TOOL_STARTED,
        STREAM_EVENT_TOOL_COMPLETED,
        STREAM_EVENT_TOOL_FAILED,
        STREAM_EVENT_RUN_COMPLETED,
        STREAM_EVENT_RUN_FAILED,
    )
    assert STREAM_EVENT_TYPES == (
        "run.started",
        "text.delta",
        "text.completed",
        "reasoning.delta",
        "reasoning.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "run.completed",
        "run.failed",
    )
    assert len(set(STREAM_EVENT_TYPES)) == len(STREAM_EVENT_TYPES)


@pytest.mark.asyncio
async def test_sdk_capture_prefers_run_level_snapshot():
    from yoreni.agent.hook import AgentHookContext, AgentRunHookContext, SDKCaptureHook
    from yoreni.providers.base import ToolCallRequest

    hook = SDKCaptureHook()
    iter_messages = [{"role": "user", "content": "work"}]
    iter_context = AgentHookContext(iteration=0, messages=iter_messages)
    iter_context.tool_calls = [
        ToolCallRequest(id="call_1", name="read_file", arguments={}),
        ToolCallRequest(id="call_2", name="grep", arguments={}),
    ]
    await hook.after_iteration(iter_context)

    final_messages = [
        {"role": "user", "content": "work"},
        {"role": "assistant", "content": "done"},
    ]
    await hook.after_run(AgentRunHookContext(
        messages=final_messages,
        tools_used=["read_file"],
        usage=LLMUsage.reported(input_tokens=3, output_tokens=0),
        stop_reason="completed",
    ))

    assert hook.tools_used == ["read_file"]
    assert hook.messages == final_messages
    assert hook.usage == LLMUsage.reported(input_tokens=3, output_tokens=0)
    assert hook.stop_reason == "completed"


@pytest.mark.asyncio
async def test_aclose_releases_loop_and_mcp_provider(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Yoreni.from_config(config_path, workspace=tmp_path)
    bot._loop.aclose = AsyncMock()
    assert bot._mcp_provider is not None
    bot._mcp_provider.aclose = AsyncMock()

    await bot.aclose()

    bot._loop.aclose.assert_awaited_once()
    bot._mcp_provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_manager_calls_aclose_on_exit(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Yoreni.from_config(config_path, workspace=tmp_path)
    bot._loop.aclose = AsyncMock()

    async with bot as b:
        assert b is bot

    bot._loop.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_manager_does_not_swallow_exceptions(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Yoreni.from_config(config_path, workspace=tmp_path)
    bot._loop.aclose = AsyncMock()

    with pytest.raises(ValueError):
        async with bot as b:
            assert b is bot
            raise ValueError("boom")

    bot._loop.aclose.assert_awaited_once()



class OfflineProvider(LLMProvider):
    def __init__(self):
        super().__init__(provider_name="offline-sdk")
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def get_default_model(self):
        return "offline-sdk"

    async def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        if str(messages[-1]["content"]).startswith("wait"):
            self.entered.set()
            await self.release.wait()
        if str(messages[-1]["content"]).startswith("use tool"):
            return LLMResponse(content=None, tool_calls=[ToolCallRequest("echo-1", "echo", {"text": "observed"})])
        return LLMResponse(content="done", usage=LLMUsage.reported(input_tokens=3, output_tokens=2))

    async def chat_stream(self, messages, *, on_content_delta=None, **kwargs):
        response = await self.chat(messages, **kwargs)
        if response.content and on_content_delta:
            await on_content_delta("do")
            await on_content_delta("ne")
        return response


class EchoTool(Tool):
    name = "echo"
    description = "Return the provided text"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    read_only = True

    async def execute(self, **kwargs):
        return kwargs["text"]


@pytest.fixture
async def agent(tmp_path):
    from yoreni.agent.loop import AgentLoop
    from yoreni.bus.queue import MessageBus

    provider = OfflineProvider()
    registry = ToolRegistry()
    registry.register(EchoTool())
    bot = Yoreni(AgentLoop(MessageBus(), provider, tmp_path, tool_registry=registry))
    # Fact extraction is covered by tests/memory; the facade tests assert
    # request/stream semantics and must not see background model calls.
    bot._loop.agent.schedule_extraction = lambda: None
    try:
        yield bot, provider
    finally:
        provider.release.set()
        await bot.aclose()


async def test_run_and_act_share_agent_continuity(agent):
    bot, provider = agent
    first = await bot.run("first request")
    second = await bot.act(AgentInput("sdk", "second request", principal="owner"))
    assert first.content == second.content == "done"
    assert first.usage == LLMUsage.reported(input_tokens=3, output_tokens=2)
    assert first.metadata["agent_run_id"] != second.metadata["agent_run_id"]
    assert any(str(row["content"]).startswith("first request") for row in provider.calls[-1])
    assert bot._loop.agent.store.read_run(first.metadata["agent_run_id"])["status"] == "completed"


async def test_ephemeral_is_a_run_option_and_does_not_retain_input(agent):
    bot, provider = agent
    result = await bot.run("private scratch", ephemeral=True)
    assert result.content == "done"
    assert result.usage == LLMUsage.reported(input_tokens=3, output_tokens=2)
    with pytest.raises(KeyError):
        bot._loop.agent.store.read_run(result.metadata["agent_run_id"])
    assert bot.memory.read() == ""
    assert bot.memory.stats()["evidence_items"] == 0
    await bot.run("later")
    assert all("private scratch" not in str(row["content"]) for row in provider.calls[-1])


async def test_run_attributes_reach_hooks_and_context_without_entering_event_storage(agent):
    bot, _ = agent
    observed = []
    def factory(context):
        observed.append(context.attributes)
    bot._loop._hook_factories.append(factory)
    result = await bot.run("hello", attributes={"application": "test"})
    assert observed[0]["application"] == "test"
    assert observed[0]["run_id"] == result.metadata["agent_run_id"]
    assert "application" not in str(bot._loop.agent.store.read_run(result.metadata["agent_run_id"])["payload"])


async def test_stream_uses_real_runner_text_tool_and_completion_events(agent):
    bot, _ = agent
    handle = await bot.run_streamed("use tool")
    events = [event async for event in handle.stream_events()]
    result = await handle.wait()
    kinds = [event.type for event in events]
    assert kinds[0] == STREAM_EVENT_RUN_STARTED
    assert kinds[-1] == STREAM_EVENT_RUN_COMPLETED
    assert STREAM_EVENT_TOOL_STARTED in kinds
    assert STREAM_EVENT_TOOL_COMPLETED in kinds
    assert "".join(event.delta for event in events if event.type == STREAM_EVENT_TEXT_DELTA) == "done"
    assert result.tools_used == ["echo"]
    assert any(row.get("role") == "tool" and row.get("content") == "observed" for row in result.messages)
    assert events[0].metadata["model"] == "offline-sdk"


async def test_stream_wait_drains_queue_and_returns_result(agent):
    bot, _ = agent
    handle = await bot.run_streamed("hello")
    assert await handle.text() == "done"
    assert handle.done
    with pytest.raises(RuntimeError, match="only be consumed once"):
        await anext(handle.stream_events())


async def test_stream_generator_break_cancels_its_run(agent):
    bot, provider = agent
    iterator = bot.stream("wait")
    first = await anext(iterator)
    assert first.type == STREAM_EVENT_RUN_STARTED
    await asyncio.wait_for(provider.entered.wait(), 5)
    await iterator.aclose()
    assert bot._loop.agent.store.pending() == []
    with bot._loop.agent.store.connection() as db:
        assert db.execute("SELECT status FROM runs").fetchone()[0] == "cancelled"


async def test_background_event_and_interaction_have_independent_hooks(agent):
    bot, provider = agent
    seen = []
    class Observe(AgentHook):
        def __init__(self, label):
            super().__init__()
            self.label = label
        async def after_run(self, context):
            seen.append((self.label, context.final_content))
    background = asyncio.create_task(bot.act(
        AgentInput("heartbeat", "wait", principal="owner", kind="internal"), hooks=[Observe("background")],
    ))
    await asyncio.wait_for(provider.entered.wait(), 5)
    try:
        await asyncio.wait_for(bot.run("hello", hooks=[Observe("user")]), 5)
        assert seen == [("user", "done")]
    finally:
        provider.release.set()
        await background
    assert seen == [("user", "done"), ("background", "done")]


async def test_stream_failure_surfaces_and_closes_handle(agent, monkeypatch):
    bot, _ = agent
    def fail(_event):
        raise OSError("database unavailable")
    monkeypatch.setattr(bot._loop.agent.store, "admit", fail)
    handle = await bot.run_streamed("hello")
    events = [event async for event in handle.stream_events()]
    assert events[-1].type == STREAM_EVENT_RUN_FAILED
    assert "database unavailable" in events[-1].error
    with pytest.raises(OSError, match="database unavailable"):
        await handle.wait()
    await handle.aclose()


async def test_model_override_is_per_run_and_reports_actual_model(agent):
    bot, _ = agent
    before = bot.runtime.model
    handle = await bot.run_streamed("hello", model="another-model")
    events = [event async for event in handle.stream_events()]
    assert (await handle.wait()).content == "done"
    assert events[0].metadata["model"] == "another-model"
    assert bot.runtime.model == before


async def test_multiple_model_selectors_rejected(agent):
    bot, _ = agent
    with pytest.raises(ValueError, match="mutually exclusive"):
        await bot.run("hello", model="one", model_preset="two")
    with pytest.raises(ValueError, match="mutually exclusive"):
        await bot.run_streamed("hello", model="one", model_preset="two")


def test_public_event_run_types():
    import yoreni
    assert yoreni.AgentInput is AgentInput
    assert yoreni.RunResult is RunResult
    assert yoreni.RunStream is RunStream


async def test_memory_client_uses_the_agent_fact_store(agent):
    bot, _ = agent
    bot._loop.agent.schedule_extraction = lambda: None
    bot.memory.write("preference")
    assert bot.memory.read() == "preference"

    result = await bot.run("I prefer Chinese.")
    fact_ids = await bot.memory.remember(
        run_id=result.metadata["agent_run_id"],
        subject="user",
        predicate="preferred_language",
        text="The user prefers Chinese.",
        kind="user_preference",
    )
    assert len(fact_ids) == 1

    hits = await bot.memory.search("Chinese")
    assert [hit["fact_id"] for hit in hits] == fact_ids
    assert hits[0]["sources"][0]["run_id"] == result.metadata["agent_run_id"]
    assert bot.memory.stats()["active_facts"] == 1


async def test_memory_client_can_cite_tool_evidence(agent):
    bot, _ = agent
    bot._loop.agent.schedule_extraction = lambda: None
    result = await bot.run("look up the latest version")
    run_id = result.metadata["agent_run_id"]
    evidence_id = bot._loop.memory.record_tool_result(
        run_id, "exec", "Yoreni version 2.4.1 is the latest release.",
    )

    [fact_id] = await bot.memory.remember(
        run_id=run_id,
        subject="project:yoreni",
        predicate="version",
        text="Yoreni is on version 2.4.1.",
        kind="project_knowledge",
        evidence_ids=[evidence_id],
    )
    hits = await bot.memory.search("Yoreni version")

    assert [hit["fact_id"] for hit in hits] == [fact_id]
    assert hits[0]["source_type"] == "tool_result"
    assert hits[0]["sources"][0]["evidence_id"] == evidence_id


async def test_cached_event_result_keeps_completion_reason(agent):
    bot, provider = agent
    event = AgentInput("sdk", "hello", principal="owner", event_id="retry")
    first = await bot.act(event)
    cached = await bot.act(event)
    assert cached.content == first.content
    assert cached.stop_reason == first.stop_reason == "completed"
    assert len(provider.calls) == 1
