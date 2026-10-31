"""Terminal capability metadata is authenticated and does not allocate tokens."""

import json
from types import SimpleNamespace

from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.channels.websocket.runtime import WebSocketConfig
from yoreni.webui.client_contract import gateway_identity
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ws_http import GatewayHTTPHandler


def test_terminal_probe_is_private_stable_and_does_not_issue_credentials():
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(token_issue_secret="fixture-secret")
    handler.tokens = GatewayTokenStore(max_tokens=0)
    connection = SimpleNamespace(remote_address=("127.0.0.1", 10000))
    request = Request("/webui/terminal", Headers({"Host": "127.0.0.1:8765"}))
    assert handler._handle_bootstrap(connection, request, terminal_probe=True).status_code == 401
    request.headers["X-Yoreni-Auth"] = "fixture-secret"
    for _ in range(3):
        response = handler._handle_bootstrap(connection, request, terminal_probe=True)
        assert response.status_code == 200
        assert json.loads(response.body) == gateway_identity(handler.tokens.instance_id)
        assert "no-store" in response.headers["Cache-Control"]
    assert not handler.tokens.api_tokens and not handler.tokens.issued_tokens
    assert GatewayTokenStore().instance_id != handler.tokens.instance_id


def test_bootstrap_accepts_a_passkey_ticket_instead_of_the_password():
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(token_issue_secret="fixture-secret")
    handler.tokens = GatewayTokenStore()
    connection = SimpleNamespace(
        remote_address=("127.0.0.1", 10000),
        respond=lambda status, _body: SimpleNamespace(status_code=status),
    )
    request = Request("/webui/terminal", Headers({"Host": "127.0.0.1:8765"}))

    ticket = handler.tokens.issue_login_ticket(60)
    request.headers["X-Yoreni-Auth"] = ticket
    assert handler._handle_bootstrap(connection, request, terminal_probe=True).status_code == 200

    request.headers["X-Yoreni-Auth"] = "nbwpt_unknown"
    assert handler._handle_bootstrap(connection, request, terminal_probe=True).status_code == 401

    # A ticket never works as a CLI token request.
    assert handler._handle_token_issue(connection, request).status_code == 401
