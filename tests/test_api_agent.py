"""Real HTTP, AgentRunner, tools and durable Agent/Event/Run state."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest
from aiohttp.test_utils import TestClient, TestServer

from yoreni.agent.loop import AgentLoop
from yoreni.agent.state import AgentInput
from yoreni.agent.tools.context import current_request_context
from yoreni.api.server import create_app
from yoreni.bus.queue import MessageBus
from yoreni.providers.base import LLMProvider, LLMResponse, ToolCallRequest

AUTH = {"Authorization": "Bearer test-api-key"}


class Provider(LLMProvider):
    def __init__(self, *, hold=False):
        super().__init__(provider_name="offline-api")
        self.calls = []
        self.hold = hold
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def get_default_model(self):
        return "offline-api"

    async def chat(self, messages, **kwargs):
        request = current_request_context()
        assert request is not None and request.agent_id and request.turn_id
        self.calls.append(deepcopy(messages))
        if self.hold and len(self.calls) == 1:
            self.entered.set()
            await self.release.wait()
        if messages[-1]["role"] != "tool":
            return LLMResponse(content=None, tool_calls=[ToolCallRequest(
                f"route-{len(self.calls)}", "my", {"action": "check", "key": "request.chat_id"},
            )])
        return LLMResponse(content="done")

    async def chat_stream(self, messages, *, on_content_delta=None, **kwargs):
        response = await self.chat(messages, **kwargs)
        if response.content and on_content_delta:
            await on_content_delta("do")
            await on_content_delta("ne")
        return response


@asynccontextmanager
async def runtime(workspace, provider):
    loop = AgentLoop(MessageBus(), provider, workspace, restrict_to_workspace=True, max_iterations=4)
    client = TestClient(TestServer(create_app(loop, api_key="test-api-key")))
    await client.start_server()
    try:
        yield loop, client
    finally:
        provider.release.set()
        await client.close()
        await loop.aclose()


async def post(client, text, *, stream=False, key=None):
    headers = dict(AUTH)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = await client.post(
        "/v1/chat/completions", headers=headers,
        json={"messages": [{"role": "user", "content": text}], "stream": stream},
    )
    assert response.status == 200, await response.text()
    if not stream:
        return (await response.json())["choices"][0]["message"]["content"]
    body = await response.text()
    assert body.endswith("data: [DONE]\n\n")
    chunks = [json.loads(line[6:]) for line in body.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)


@pytest.mark.parametrize("stream", [False, True])
async def test_http_input_runs_tools_and_retains_agent_context(tmp_path, stream):
    provider = Provider()
    async with runtime(tmp_path, provider) as (loop, client):
        assert await post(client, "first input", stream=stream) == "done"
        identity = loop.agent.store.identity()
        with loop.agent.store.connection() as db:
            records = db.execute("SELECT payload FROM events").fetchall()
        assert len(records) == 1
        assert json.loads(records[0][0])["source"] == "api"
        assert any(row["role"] == "tool" for row in loop.agent.store.recent())
    async with runtime(tmp_path, Provider()) as (loop, client):
        assert loop.agent.store.identity() == identity
        assert await post(client, "after restart", stream=stream) == "done"
        assert any(str(row["content"]).startswith("first input") for row in loop.provider.calls[-1])


async def test_http_retry_reuses_run_and_conflicting_payload_is_409(tmp_path):
    provider = Provider()
    async with runtime(tmp_path, provider) as (loop, client):
        assert await post(client, "hello", key="request-1") == "done"
        assert await post(client, "hello", key="request-1") == "done"
        assert len(provider.calls) == 2
        conflict = await client.post(
            "/v1/chat/completions", headers={**AUTH, "Idempotency-Key": "request-1"},
            json={"messages": [{"role": "user", "content": "changed"}]},
        )
        assert conflict.status == 409
        with loop.agent.store.connection() as db:
            assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


async def test_queued_http_input_is_durable_and_executed_after_prior_user_run(tmp_path):
    provider = Provider(hold=True)
    async with runtime(tmp_path, provider) as (loop, client):
        first = asyncio.create_task(post(client, "first"))
        await asyncio.wait_for(provider.entered.wait(), 5)
        second = asyncio.create_task(post(client, "second"))
        try:
            async with asyncio.timeout(5):
                while len(loop.agent.store.pending()) < 2:
                    await asyncio.sleep(0.01)
            assert len(provider.calls) == 1
            assert not second.done()
        finally:
            provider.release.set()
            assert await first == "done"
            assert await second == "done"
        assert any(str(row["content"]).startswith("first") for row in provider.calls[-1])


async def test_internal_event_does_not_block_http_interaction(tmp_path):
    provider = Provider(hold=True)
    async with runtime(tmp_path, provider) as (loop, client):
        background = asyncio.create_task(loop.process_event(
            AgentInput("heartbeat", "check tasks", principal="owner", kind="internal"),
        ))
        await asyncio.wait_for(provider.entered.wait(), 5)
        try:
            assert await asyncio.wait_for(post(client, "hello"), 5) == "done"
            assert not background.done()
        finally:
            provider.release.set()
            await background


@pytest.mark.parametrize("stream", [False, True])
async def test_removed_routing_parameter_rejected_at_http_boundary(tmp_path, stream):
    provider = Provider()
    async with runtime(tmp_path, provider) as (_, client):
        response = await client.post(
            "/v1/chat/completions", headers=AUTH,
            json={"session_id": "old-client", "stream": stream,
                  "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status == 400
        assert "no longer supported" in (await response.json())["error"]["message"]
        assert provider.calls == []


async def test_authentication_precedes_personal_execution(tmp_path):
    provider = Provider()
    async with runtime(tmp_path, provider) as (_, client):
        response = await client.post(
            "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status == 401
        assert provider.calls == []
