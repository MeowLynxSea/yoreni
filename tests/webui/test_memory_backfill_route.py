"""Memory update snapshot and pause/resume routes."""

import json
import time
from types import SimpleNamespace

from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ws_http import GatewayHTTPHandler


def handler_for(action=None) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    tokens = GatewayTokenStore(max_tokens=4)
    tokens.api_tokens["fixture-token"] = time.monotonic() + 60
    handler.tokens = tokens
    handler.memory_backfill_action = action
    handler.settings_routes = SimpleNamespace(is_mutation_path=lambda _path: False)
    return handler


def request_for(path: str, payload=None) -> Request:
    request = Request(path, Headers({"Authorization": "Bearer fixture-token"}))
    if payload is not None:
        request._yoreni_webui_mutation_payload = payload
    return request


SNAPSHOT = {
    "active": True,
    "status": "running",
    "model": "bge-m3",
    "done": 3,
    "total": 10,
    "remaining": 7,
    "eta_ms": 42000,
}


async def test_snapshot_returns_the_task_state():
    calls: list[tuple[str, dict]] = []

    async def action(name, payload):
        calls.append((name, payload))
        return SNAPSHOT

    response = await handler_for(action)._handle_memory_backfill(
        request_for("/api/webui/memory-backfill"), mutation=False,
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"memory_backfill": SNAPSHOT}
    assert calls == [("snapshot", {})]


async def test_pause_and_resume_forward_the_requested_state():
    seen: list[str] = []

    async def action(name, payload):
        seen.append(name)
        return {**SNAPSHOT, "status": "paused" if name == "pause" else "running"}

    handler = handler_for(action)
    paused = await handler._handle_memory_backfill(
        request_for("/api/webui/memory-backfill/state", {"paused": True}), mutation=True,
    )
    resumed = await handler._handle_memory_backfill(
        request_for("/api/webui/memory-backfill/state", {"paused": False}), mutation=True,
    )

    assert json.loads(paused.body)["memory_backfill"]["status"] == "paused"
    assert json.loads(resumed.body)["memory_backfill"]["status"] == "running"
    assert seen == ["pause", "resume"]


async def test_state_requires_an_explicit_flag():
    async def action(name, payload):
        raise AssertionError("an explicit flag is required")

    response = await handler_for(action)._handle_memory_backfill(
        request_for("/api/webui/memory-backfill/state", {}), mutation=True,
    )

    assert response.status_code == 400


async def test_the_route_requires_a_token():
    response = await handler_for()._handle_memory_backfill(
        Request("/api/webui/memory-backfill", Headers()), mutation=False,
    )

    assert response.status_code == 401


def test_state_mutation_is_websocket_only():
    handler = handler_for()
    path = handler._webui_mutation_path("memory.backfill.state", {"paused": True})

    assert path == "/api/webui/memory-backfill/state"
    assert handler._is_webui_mutation_path(path)


class _Transport:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def webui_recipients(self, _audience):
        return ("connection",)

    async def webui_send_raw(self, _connection, raw, *, label=""):
        self.frames.append(json.loads(raw))


async def test_progress_reaches_the_websocket_frames():
    from yoreni.agent.state import PERSONAL_CHAT_ID
    from yoreni.bus.outbound_events import MemoryBackfillEvent, outbound_message_for_event
    from yoreni.webui.outbound_delivery import WebUIOutboundDelivery

    transport = _Transport()
    delivery = WebUIOutboundDelivery(transport, media=None)  # type: ignore[arg-type]
    message = outbound_message_for_event(
        channel="websocket", chat_id=PERSONAL_CHAT_ID, event=MemoryBackfillEvent(SNAPSHOT),
    )

    await delivery.send(message)

    assert transport.frames == [{"event": "memory_backfill", "memory_backfill": SNAPSHOT}]
