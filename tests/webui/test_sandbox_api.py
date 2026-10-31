"""Access boundary for the execution plane's WebUI routes."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from yoreni.bus.queue import MessageBus
from yoreni.channels.websocket.runtime import WebSocketConfig
from yoreni.sandbox.desktop_proxy import get_desktop_proxy
from yoreni.sandbox.gateway import get_sandbox_gateway
from yoreni.webui.gateway_endpoint import WebUIGatewayEndpoint
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ws_http import GatewayHTTPHandler


class _SettingsRoutes:
    def is_mutation_path(self, _path: str) -> bool:
        return False


def _handler(connection: Any = None) -> GatewayHTTPHandler:
    del connection
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(path="/ws", token="fixture-token")
    handler.tokens = GatewayTokenStore(max_tokens=4)
    handler.tokens.api_tokens["fixture-token"] = time.monotonic() + 60
    handler.bus = MessageBus()
    handler.settings_routes = _SettingsRoutes()
    handler.static_dist_path = None
    handler._log = SimpleNamespace(warning=lambda *_args, **_kwargs: None)
    return handler


def _request(path: str, *, token: str | None = None) -> WsRequest:
    headers = Headers({"Authorization": f"Bearer {token}"} if token else {})
    return cast(WsRequest, SimpleNamespace(path=path, headers=headers))


async def _dispatch(path: str, *, token: str | None = None) -> Any:
    handler = _handler()
    return await handler.dispatch(cast(Any, None), _request(path, token=token))


async def test_sandbox_routes_reject_requests_without_an_api_token() -> None:
    for path in ("/api/sandbox/status", "/api/sandbox/desktop/session"):
        response = await _dispatch(path)
        assert response.status_code == 401, path


async def test_plain_http_cannot_change_desktop_control() -> None:
    # Control changes are mutations of an authenticated socket, never a URL a
    # page can trigger on its own.
    for path in ("/api/sandbox/control/take", "/api/sandbox/control/release"):
        response = await _dispatch(f"{path}?controller=nbdk_forged", token="fixture-token")
        assert response.status_code == 405, path


async def test_status_route_reports_the_bound_gateway() -> None:
    response = await _dispatch("/api/sandbox/status", token="fixture-token")

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["sandbox"]["workspace"] == "/"
    assert payload["control"]["owner"] == "agent"
    assert payload["desktop"]["viewer_path"] == "/api/sandbox/desktop/ws"


async def _mutate(action: str, payload: dict[str, Any]) -> Any:
    handler = _handler()
    connection = cast(Any, SimpleNamespace(request=None))
    return await handler.dispatch_webui_mutation(connection, action, payload)


async def test_control_mutations_only_accept_an_issued_controller(monkeypatch) -> None:
    gateway = get_sandbox_gateway()
    calls: list[bool] = []

    async def record_view_only(view_only: bool) -> None:
        calls.append(view_only)

    monkeypatch.setattr(gateway.desktop, "set_view_only", record_view_only)

    unknown = await _mutate("sandbox.control.take", {"controller": "nbdk_forged0000000000"})
    assert unknown.status_code == 403

    session = await _dispatch("/api/sandbox/desktop/session", token="fixture-token")
    ticket = json.loads(session.body)["ticket"]
    assert get_desktop_proxy().authorize(ticket) == ticket

    taken = await _mutate("sandbox.control.take", {"controller": ticket})
    assert taken.status_code == 200
    assert json.loads(taken.body)["control"]["owner"] == "user"

    released = await _mutate("sandbox.control.release", {"controller": ticket})
    assert released.status_code == 200
    assert json.loads(released.body)["control"]["owner"] == "agent"
    # Input opens only while the user owns the desktop, then closes again.
    assert calls == [False, True]


async def test_control_mutation_rejects_a_malformed_controller() -> None:
    response = await _mutate("sandbox.control.take", {"controller": "../../etc/passwd"})

    assert response.status_code == 400


async def test_unknown_sandbox_route_is_a_404() -> None:
    response = await _dispatch("/api/sandbox/control/grant", token="fixture-token")

    assert response.status_code == 404


class _Connection:
    def __init__(self) -> None:
        self.status: int | None = None
        self.reason = ""

    def respond(self, status: int, reason: str = "") -> Any:
        self.status = status
        self.reason = reason
        return self


def _upgrade_request(ticket: str | None = None) -> WsRequest:
    query = f"?ticket={ticket}" if ticket is not None else ""
    headers = Headers({"Upgrade": "websocket", "Connection": "Upgrade"})
    return cast(
        WsRequest,
        SimpleNamespace(path=f"/api/sandbox/desktop/ws{query}", headers=headers),
    )


def _endpoint() -> WebUIGatewayEndpoint:
    return WebUIGatewayEndpoint(
        config=WebSocketConfig(path="/ws", token="fixture-token"),
        http=_handler(),
        tokens=GatewayTokenStore(max_tokens=4),
    )


async def test_desktop_socket_requires_a_fresh_ticket() -> None:
    endpoint = _endpoint()

    missing = _Connection()
    assert await endpoint.process_request(
        cast(Any, missing), _upgrade_request(), is_allowed=lambda _cid: True,
    ) is missing
    assert missing.status == 401

    ticket = get_desktop_proxy().issue_ticket()
    accepted = _Connection()
    assert await endpoint.process_request(
        cast(Any, accepted), _upgrade_request(ticket), is_allowed=lambda _cid: True,
    ) is None
    assert accepted._yoreni_desktop_controller == ticket

    # The same ticket cannot open a second viewer.
    replay = _Connection()
    await endpoint.process_request(
        cast(Any, replay), _upgrade_request(ticket), is_allowed=lambda _cid: True,
    )
    assert replay.status == 401


@pytest.mark.parametrize("ticket", ["", "nbdk_forged"])
async def test_desktop_socket_rejects_forged_tickets(ticket: str) -> None:
    connection = _Connection()

    await _endpoint().process_request(
        cast(Any, connection), _upgrade_request(ticket), is_allowed=lambda _cid: True,
    )

    assert connection.status == 401
