"""Home widget layout persistence and its WebUI route."""

import json
import time

from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.webui import home_widgets
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ws_http import GatewayHTTPHandler


def handler_for() -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    tokens = GatewayTokenStore(max_tokens=4)
    tokens.api_tokens["fixture-token"] = time.monotonic() + 60
    handler.tokens = tokens
    return handler


def request_for(path: str = "/api/webui/home-widgets", payload=None) -> Request:
    request = Request(path, Headers({"Authorization": "Bearer fixture-token"}))
    if payload is not None:
        request._yoreni_webui_mutation_payload = payload
    return request


def test_route_lists_an_empty_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(home_widgets, "get_webui_dir", lambda: tmp_path)

    response = handler_for()._handle_home_widgets(request_for())

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "schema_version": 1,
        "updated_at": None,
        "order": [],
        "widgets": {},
    }


def test_route_saves_the_layout_and_reads_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(home_widgets, "get_webui_dir", lambda: tmp_path)
    handler = handler_for()
    layout = {
        "order": ["automations", "goal"],
        "widgets": {
            "goal": {"visible": False, "collapsed": True},
            "automations": {"visible": True, "collapsed": False},
        },
    }

    saved = handler._handle_home_widgets(request_for(payload=layout))

    assert saved.status_code == 200
    body = json.loads(saved.body)
    assert body["order"] == ["automations", "goal"]
    assert body["widgets"] == layout["widgets"]
    assert isinstance(body["updated_at"], str)
    assert json.loads(handler._handle_home_widgets(request_for()).body) == body


def test_route_rejects_an_order_that_skips_a_widget(tmp_path, monkeypatch):
    monkeypatch.setattr(home_widgets, "get_webui_dir", lambda: tmp_path)

    response = handler_for()._handle_home_widgets(request_for(payload={
        "order": ["goal"],
        "widgets": {
            "goal": {"visible": True, "collapsed": False},
            "automations": {"visible": True, "collapsed": False},
        },
    }))

    assert response.status_code == 400


def test_route_requires_a_token(tmp_path, monkeypatch):
    monkeypatch.setattr(home_widgets, "get_webui_dir", lambda: tmp_path)

    response = handler_for()._handle_home_widgets(
        Request("/api/webui/home-widgets", Headers()),
    )

    assert response.status_code == 401


def test_normalize_drops_malformed_entries():
    layout = home_widgets.normalize_home_widget_layout({
        "order": ["goal", "missing", "goal"],
        "widgets": {
            "goal": {"visible": True, "collapsed": "yes"},
            "automations": {"visible": False, "collapsed": True},
            "not a widget id": {"visible": True, "collapsed": False},
        },
    })

    assert layout["widgets"] == {"automations": {"visible": False, "collapsed": True}}
    assert layout["order"] == ["automations"]
