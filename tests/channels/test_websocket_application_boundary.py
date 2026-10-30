"""Behavioral delegation and executable ownership boundaries for WebSocket."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from yoreni.bus.events import OutboundMessage
from yoreni.bus.queue import MessageBus
from yoreni.channels.websocket import runtime
from yoreni.webui.gateway_services import build_gateway_services


@pytest.fixture
async def channel(tmp_path, monkeypatch):
    monkeypatch.setattr("yoreni.config.paths.get_data_dir", lambda: tmp_path)
    config = runtime.WebSocketConfig(allow_from=["client-1"], websocket_requires_token=False)
    bus = MessageBus()
    gateway = build_gateway_services(
        config=config,
        bus=bus,
        static_dist_path=None,
        workspace_path=tmp_path,
        default_restrict_to_workspace=True,
        config_path=tmp_path / "config.json",
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
    )
    channel = runtime.WebSocketChannel(config, bus, gateway=gateway)
    try:
        yield channel
    finally:
        async with asyncio.timeout(2):
            await channel.stop()
        assert channel._server_task is None
        assert not channel._connection_outbound
        assert not channel._outbound_retire_tasks
        assert not channel._commands.request_tasks


async def test_send_delegates_the_outbound_message_to_delivery(channel, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(channel._outbound, "send", send)
    msg = OutboundMessage(
        channel="websocket", chat_id="chat-1", content="hello", reply_to="m1",
        media=["/example/image.png"], metadata={"turn_id": "turn-1"},
    )

    await channel.send(msg)

    send.assert_awaited_once_with(msg)


@pytest.mark.parametrize(
    ("method", "args", "kwargs"),
    [
        ("send_delta", ("chat-1", "part", {"webui_turn_id": "turn-1"}), {
            "stream_id": "s1", "stream_end": True, "resuming": True, "merge_next": True,
        }),
        ("send_reasoning_delta", ("chat-1", "plan", {"webui_turn_id": "turn-1"}), {"stream_id": "r1"}),
        ("send_reasoning_end", ("chat-1", {"webui_turn_id": "turn-1"}), {"stream_id": "r1"}),
        ("send_file_edit_events", ("chat-1", [{"path": "a.py"}], {"webui_turn_id": "turn-1"}), {}),
    ],
)
async def test_streaming_capabilities_use_the_same_delivery_owner(channel, monkeypatch, method, args, kwargs):
    deliver = AsyncMock()
    monkeypatch.setattr(channel._outbound, method, deliver)

    await getattr(channel, method)(*args, **kwargs)

    deliver.assert_awaited_once_with(*args, **kwargs)


async def test_envelope_delegates_connection_client_and_payload_to_application(channel, monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(channel._commands, "dispatch", dispatch)
    connection = AsyncMock()
    envelope = {"type": "subscribe", "chat_id": "chat-2"}

    await channel._dispatch_envelope(connection, "client-1", envelope)

    dispatch.assert_awaited_once_with(connection, "client-1", envelope)


@pytest.mark.parametrize("allow_upgrade", [False, True])
async def test_http_delegates_authorization_and_preserves_endpoint_result(
    channel, monkeypatch, allow_upgrade,
):
    response = None if allow_upgrade else Response(
        403, "Forbidden", Headers({"Content-Type": "text/plain"}), b"access denied",
    )
    process_request = AsyncMock(return_value=response)
    monkeypatch.setattr(channel.gateway.endpoint, "process_request", process_request)
    connection = AsyncMock()
    request = Request("/webui/thread?chat_id=chat-2", Headers())

    result = await channel._dispatch_http(connection, request)

    assert result is response
    process_request.assert_awaited_once_with(connection, request, is_allowed=channel.is_allowed)
    authorize = process_request.await_args.kwargs["is_allowed"]
    assert authorize("client-1") is True
    assert authorize("other-client") is False


def test_runtime_exports_compatibility_protocol_helpers() -> None:
    assert runtime._is_valid_chat_id("unified:default")  # pyright: ignore[reportPrivateUsage]
    assert not runtime._is_valid_chat_id("../escape")  # pyright: ignore[reportPrivateUsage]
