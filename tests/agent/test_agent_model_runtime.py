"""Runtime selection belongs to Agent preferences or a single Run override."""

from __future__ import annotations

import asyncio

import pytest

from yoreni import AgentInput, Yoreni
from yoreni.agent.loop import AgentLoop
from yoreni.agent.persistent import resource_lock
from yoreni.bus.queue import MessageBus
from yoreni.config.schema import ModelPresetConfig
from yoreni.providers.base import GenerationSettings, LLMProvider, LLMResponse
from yoreni.providers.factory import ProviderSnapshot
from yoreni.utils.llm_runtime import LLMRuntime


class Provider(LLMProvider):
    def __init__(self, name):
        super().__init__(provider_name=name)
        self.generation = GenerationSettings(max_tokens=256, temperature=0.1)
        self.name = name
        self.calls = []

    def get_default_model(self):
        return self.name

    async def chat(self, messages, *, model=None, **kwargs):
        await asyncio.sleep(0)
        self.calls.append(model)
        return LLMResponse(content=f"reply from {self.name}")


@pytest.fixture
async def runtime(tmp_path):
    providers = {name: Provider(name) for name in ("base", "fast", "deep")}
    presets = {name: ModelPresetConfig(model=name, context_window_tokens=128000) for name in ("fast", "deep")}
    def load(name):
        return ProviderSnapshot(providers[name], name, 128000, signature=(name,))
    loop = AgentLoop(
        MessageBus(), providers["base"], tmp_path,
        model_presets=presets, preset_snapshot_loader=load,
    )
    try:
        yield loop, providers
    finally:
        await loop.aclose()


async def test_agent_model_preference_survives_restart(runtime):
    loop, providers = runtime
    store = loop.agent.store
    identity = store.identity()
    store.update(identity.revision, {"model_preset": "fast"})
    assert (await Yoreni(loop).run("hello")).content == "reply from fast"
    assert providers["fast"].calls == ["fast"]
    assert providers["base"].calls == []
    from yoreni.agent.state import AgentStore
    assert AgentStore(loop.workspace).identity().state["model_preset"] == "fast"


async def test_run_override_does_not_mutate_agent_model_preference(runtime):
    loop, providers = runtime
    store = loop.agent.store
    store.update(store.identity().revision, {"model_preset": "fast"})
    override = LLMRuntime.capture(providers["deep"], "deep", context_window_tokens=128000)
    result = await loop.process_event(AgentInput("sdk", "hello", principal="owner"), runtime=override)
    assert result.content == "reply from deep"
    assert store.identity().state["model_preset"] == "fast"
    assert (await Yoreni(loop).run("next")).content == "reply from fast"


async def test_background_runs_use_independent_runtime_snapshots(runtime):
    loop, providers = runtime
    replies = await asyncio.gather(*[
        loop.process_event(
            AgentInput("scheduled-task", name, principal="owner", kind="internal"),
            runtime=LLMRuntime.capture(providers[name], name, context_window_tokens=128000),
        ) for name in ("fast", "deep")
    ])
    assert [reply.content for reply in replies] == ["reply from fast", "reply from deep"]
    assert loop.model == "base"


async def test_stream_runtime_is_selected_after_durable_queue_admission(runtime):
    loop, providers = runtime
    store = loop.agent.store
    store.update(store.identity().revision, {"model_preset": "fast"})
    async with resource_lock(store, "interaction"):
        handle = await Yoreni(loop).run_streamed("queued input")
        async with asyncio.timeout(5):
            while not store.pending():
                await asyncio.sleep(0.01)
        store.update(store.identity().revision, {"model_preset": "deep"})
    events = [event async for event in handle.stream_events()]
    assert (await handle.wait()).content == "reply from deep"
    assert events[0].metadata["model"] == "deep"
    assert events[0].metadata["model_preset"] == "deep"
    assert providers["fast"].calls == []
