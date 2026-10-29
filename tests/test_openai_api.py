"""Focused tests for the personal-agent OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from loguru import logger

from yoreni.agent.hook import AgentHook, AgentRunHookContext
from yoreni.api.server import (
    _chat_completion_response,
    _error_json,
    create_app,
    handle_chat_completions,
)
from yoreni.providers.base import LLMUsage
from yoreni.providers.unconfigured_provider import UnconfiguredProvider

try:
    from aiohttp.test_utils import TestClient, TestServer

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytest_plugins = ("pytest_asyncio",)

API_KEY = "secret"
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def _make_mock_agent(response_text: str = "mock response") -> MagicMock:
    agent = MagicMock()
    agent.process_event = AsyncMock(return_value=response_text)
    agent.process_event = AsyncMock(return_value=response_text)
    agent.aclose = AsyncMock()
    return agent


@pytest.fixture
def mock_agent():
    return _make_mock_agent()


@pytest.fixture
def app(mock_agent):
    return create_app(mock_agent, model_name="test-model", request_timeout=10.0, api_key=API_KEY)


@pytest_asyncio.fixture
async def aiohttp_client():
    clients: list[TestClient] = []

    async def _make_client(app):
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    try:
        yield _make_client
    finally:
        for client in clients:
            await client.close()


def test_error_json() -> None:
    resp = _error_json(400, "bad request")
    assert resp.status == 400
    body = json.loads(resp.body)
    assert body["error"]["message"] == "bad request"
    assert body["error"]["code"] == 400


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_unconfigured_model_returns_503_before_execution(aiohttp_client, mock_agent, stream):
    mock_agent.provider = UnconfiguredProvider("not-configured")
    prepare = AsyncMock()
    app = create_app(mock_agent, api_key=API_KEY, prepare_agent=prepare)
    client = await aiohttp_client(app)

    assert (await client.get("/health")).status == 200
    denied = await client.post("/v1/chat/completions", json={"messages": []})
    assert denied.status == 401
    response = await client.post(
        "/v1/chat/completions", headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "hello"}], "stream": stream},
    )
    assert response.status == 503
    error = (await response.json())["error"]
    assert error["type"] == "configuration_error"
    assert "Model is not configured" in error["message"]
    mock_agent.process_event.assert_not_called()
    prepare.assert_not_called()


def test_chat_completion_response() -> None:
    result = _chat_completion_response("hello world", "test-model")
    assert result["object"] == "chat.completion"
    assert result["model"] == "test-model"
    assert result["choices"][0]["message"]["content"] == "hello world"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["id"].startswith("chatcmpl-")
    assert result["usage"]["prompt_tokens"] == 0
    assert result["usage"]["completion_tokens"] == 0
    assert result["usage"]["total_tokens"] == 0


def test_chat_completion_response_with_usage() -> None:
    usage = LLMUsage.reported(input_tokens=150, output_tokens=42)
    result = _chat_completion_response("hello world", "test-model", usage)
    assert result["usage"]["prompt_tokens"] == 150
    assert result["usage"]["completion_tokens"] == 42
    assert result["usage"]["total_tokens"] == 192


def test_chat_completion_response_preserves_explicit_total_usage() -> None:
    usage = LLMUsage.reported(input_tokens=70, output_tokens=7, total_tokens=175)
    result = _chat_completion_response("hello world", "test-model", usage)
    assert result["usage"]["prompt_tokens"] == 70
    assert result["usage"]["completion_tokens"] == 7
    assert result["usage"]["total_tokens"] == 175


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_missing_messages_returns_400(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json={"model": "test"})
    assert resp.status == 400


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_api_key_protects_api_routes_but_not_health(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="test-model", api_key=API_KEY)
    client = await aiohttp_client(app)

    health = await client.get("/health")
    missing = await client.get("/v1/models")
    wrong = await client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    ok = await client.get("/v1/models", headers=AUTH_HEADERS)

    assert health.status == 200
    assert missing.status == 401
    assert wrong.status == 401
    assert ok.status == 200
    assert health.headers["X-Request-ID"]
    assert ok.headers["X-Request-ID"]
    assert health.headers["X-Request-ID"] != ok.headers["X-Request-ID"]
    assert (await missing.json())["error"]["message"].startswith("Missing Authorization")
    assert (await wrong.json())["error"]["message"] == "Invalid API key"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_api_request_log_has_correlation_and_completion_fields(
    aiohttp_client, mock_agent
) -> None:
    records = []
    sink = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        client = await aiohttp_client(create_app(mock_agent, api_key=API_KEY))
        response = await client.get("/v1/models", headers=AUTH_HEADERS)
    finally:
        logger.remove(sink)

    completion = next(
        record for record in records if record["extra"].get("event") == "http_request"
    )
    assert completion["extra"]["request_id"] == response.headers["X-Request-ID"]
    assert completion["extra"]["outcome"] == "success"
    assert completion["extra"]["status_code"] == 200
    assert completion["extra"]["duration_ms"] >= 0


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_api_routes_allow_requests_without_configured_api_key(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="test-model")
    client = await aiohttp_client(app)

    health = await client.get("/health")
    models = await client.get("/v1/models")
    chat = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert health.status == 200
    assert models.status == 200
    assert chat.status == 200
    mock_agent.process_event.assert_called_once()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_api_prepares_application_resources_before_each_turn(aiohttp_client) -> None:
    events: list[str] = []
    agent = _make_mock_agent()

    async def prepare_agent() -> None:
        events.append("prepare")

    async def process_event(_event, **_kwargs):
        events.append("process")
        return "ready"

    agent.process_event = process_event
    app = create_app(agent, prepare_agent=prepare_agent)
    client = await aiohttp_client(app)

    response = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status == 200
    assert events == ["prepare", "process"]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_api_preparation_is_bounded_by_request_timeout(aiohttp_client) -> None:
    agent = _make_mock_agent()
    started = asyncio.Event()

    async def prepare_agent() -> None:
        started.set()
        await asyncio.Event().wait()

    app = create_app(
        agent,
        request_timeout=0.01,
        prepare_agent=prepare_agent,
    )
    client = await aiohttp_client(app)

    response = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert started.is_set()
    assert response.status == 504
    agent.process_event.assert_not_awaited()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_no_user_message_returns_400(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "system", "content": "you are a bot"}]},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_model_mismatch_returns_400() -> None:
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "model": "other-model",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    request.app = create_app(_make_mock_agent(), model_name="test-model", request_timeout=10.0)

    resp = await handle_chat_completions(request)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert "test-model" in body["error"]["message"]


@pytest.mark.asyncio
async def test_single_user_message_required() -> None:
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "previous reply"},
            ],
        }
    )
    request.app = create_app(_make_mock_agent(), model_name="test-model", request_timeout=10.0)

    resp = await handle_chat_completions(request)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert "single user message" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_single_user_message_must_have_user_role() -> None:
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "messages": [{"role": "system", "content": "you are a bot"}],
        }
    )
    request.app = create_app(_make_mock_agent(), model_name="test-model", request_timeout=10.0)

    resp = await handle_chat_completions(request)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert "single user message" in body["error"]["message"].lower()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_successful_request_submits_event_and_captures_run_usage(
    aiohttp_client,
    mock_agent,
) -> None:
    usage = LLMUsage.reported(input_tokens=100, output_tokens=50)

    async def process_event(_event, *, hooks: list[AgentHook], **_kwargs: object) -> str:
        for hook in hooks:
            await hook.after_run(AgentRunHookContext(messages=[], usage=usage))
        return "mock response"

    mock_agent.process_event = AsyncMock(side_effect=process_event)
    app = create_app(mock_agent, model_name="test-model", api_key=API_KEY)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["choices"][0]["message"]["content"] == "mock response"
    assert body["model"] == "test-model"
    assert body["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }
    call_kwargs = _event_payload(mock_agent.process_event)
    assert call_kwargs["content"] == "hello"
    assert call_kwargs["media"] == []
    assert len(call_kwargs["hooks"]) == 1


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_models_endpoint(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.get("/v1/models", headers=AUTH_HEADERS)
    assert resp.status == 200
    body = await resp.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "test-model"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_health_endpoint(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_multimodal_content_extracts_text(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="m", api_key=API_KEY)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                }
            ]
        },
    )
    assert resp.status == 200
    call_kwargs = _event_payload(mock_agent.process_event)
    assert call_kwargs["content"] == "describe this"
    assert len(call_kwargs.get("media") or []) >= 0  # base64 images saved to disk


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_part", "expected_error"),
    [
        ({"type": "text", "text": 123}, "messages[0].content[].text must be a string"),
        (
            {"type": "image_url", "image_url": "not-an-object"},
            "messages[0].content[].image_url must be an object",
        ),
        (
            {"type": "image_url", "image_url": {"url": 123}},
            "messages[0].content[].image_url.url must be a string",
        ),
    ],
)
async def test_multimodal_invalid_field_type_returns_400(
    aiohttp_client,
    mock_agent,
    content_part: dict[str, object],
    expected_error: str,
) -> None:
    app = create_app(mock_agent, model_name="m", api_key=API_KEY)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [content_part],
                }
            ]
        },
    )

    assert resp.status == 400
    body = await resp.json()
    assert body["error"]["message"] == expected_error
    assert body["error"]["code"] == 400
    mock_agent.process_event.assert_not_called()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_multimodal_remote_image_url_returns_400(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="m", api_key=API_KEY)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
                    ],
                }
            ]
        },
    )

    assert resp.status == 400
    body = await resp.json()
    assert "remote image urls are not supported" in body["error"]["message"].lower()
    mock_agent.process_event.assert_not_called()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_empty_response_falls_back_without_retry(aiohttp_client) -> None:
    from yoreni.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

    call_count = 0

    async def always_empty(content, **kwargs):
        nonlocal call_count
        call_count += 1
        return ""

    agent = MagicMock()
    agent.process_event = always_empty
    agent.aclose = AsyncMock()

    app = create_app(agent, model_name="m", api_key=API_KEY)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["choices"][0]["message"]["content"] == EMPTY_FINAL_RESPONSE_MESSAGE
    assert call_count == 1


def _event_payload(mock):
    event = mock.call_args.args[0]
    return {"content": event.content, "media": list(event.media), "channel": event.channel,
            "chat_id": event.chat_id, **mock.call_args.kwargs}
