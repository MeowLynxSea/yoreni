"""Dynamic widget HTTP routes and change delivery."""

import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.bus.outbound_events import WidgetChangedEvent
from yoreni.bus.queue import MessageBus
from yoreni.webui import home_widgets
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.run_events import publish_widget_change
from yoreni.webui.ws_http import GatewayHTTPHandler
from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.service import WidgetService
from yoreni.widgets.store import WidgetStore
from yoreni.widgets.types import MAX_WIDGETS, WidgetChange, WidgetSpec

pytestmark = pytest.mark.usefixtures("vm_workspace")


def handler_for(service: WidgetService | None = None) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    tokens = GatewayTokenStore(max_tokens=4)
    tokens.api_tokens["fixture-token"] = time.monotonic() + 60
    handler.tokens = tokens
    handler.widget_service = service
    handler.settings_routes = SimpleNamespace(is_mutation_path=lambda _path: False)
    return handler


def request_for(path: str, payload=None) -> Request:
    request = Request(path, Headers({"Authorization": "Bearer fixture-token"}))
    if payload is not None:
        request._yoreni_webui_mutation_payload = payload
    return request


def make_service(tmp_path) -> WidgetService:
    return WidgetService(
        WidgetStore(tmp_path / "widgets"),
        runner=WidgetRunner(),
        workspace=tmp_path,
    )


async def seed_widget(service: WidgetService) -> None:
    await service.create(
        WidgetSpec(
            id="runs",
            title="Runs",
            description="Today",
            view="stat",
            command=(
                sys.executable,
                "-c",
                "import json; print(json.dumps({'value': 3, 'label': 'Runs'}))",
            ),
        )
    )


async def test_list_returns_widget_summaries(tmp_path):
    service = make_service(tmp_path)
    await seed_widget(service)

    response = handler_for(service)._handle_widgets(request_for("/api/webui/widgets"))

    assert response.status_code == 200
    body = json.loads(response.body)
    assert [widget["id"] for widget in body["widgets"]] == ["runs"]
    widget = body["widgets"][0]
    assert widget["title"] == "Runs"
    assert widget["view"] == "stat"
    assert widget["has_command"] is True
    assert widget["has_data"] is True
    assert widget["error"] is None


async def test_detail_returns_data_without_the_workspace_path(tmp_path):
    service = make_service(tmp_path)
    await seed_widget(service)

    response = handler_for(service)._handle_widget_detail(
        request_for("/api/webui/widgets/runs"), "runs"
    )

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["data"] == {"value": 3, "label": "Runs"}
    assert body["html"] is None
    assert "workspace" not in body
    assert "workspace" not in body["widget"]
    assert str(tmp_path.resolve()) not in response.body.decode()


def test_detail_reports_a_missing_widget(tmp_path):
    service = make_service(tmp_path)

    response = handler_for(service)._handle_widget_detail(
        request_for("/api/webui/widgets/ghost"), "ghost"
    )

    assert response.status_code == 404


def test_routes_require_a_token(tmp_path):
    service = make_service(tmp_path)

    response = handler_for(service)._handle_widgets(
        Request("/api/webui/widgets", Headers())
    )

    assert response.status_code == 401


def test_routes_report_an_absent_service():
    response = handler_for()._handle_widgets(request_for("/api/webui/widgets"))

    assert response.status_code == 503


async def test_refresh_runs_the_command_again(tmp_path):
    service = make_service(tmp_path)
    await seed_widget(service)

    response = await handler_for(service)._handle_widget_mutation(
        request_for("/api/webui/widgets/refresh", {"widget_id": "runs"}),
        "/api/webui/widgets/refresh",
    )

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["widget_id"] == "runs"
    assert body["data"] == {"value": 3, "label": "Runs"}


async def test_refresh_reports_a_failing_command(tmp_path):
    service = make_service(tmp_path)
    await service.create(
        WidgetSpec(
            id="runs",
            title="Runs",
            view="stat",
            command=(sys.executable, "-c", "import sys; sys.exit(5)"),
        )
    )

    response = await handler_for(service)._handle_widget_mutation(
        request_for("/api/webui/widgets/refresh", {"widget_id": "runs"}),
        "/api/webui/widgets/refresh",
    )

    assert response.status_code == 500
    assert b"status 5" in response.body


async def test_delete_removes_the_widget(tmp_path):
    service = make_service(tmp_path)
    await seed_widget(service)

    response = await handler_for(service)._handle_widget_mutation(
        request_for("/api/webui/widgets/delete", {"widget_id": "runs"}),
        "/api/webui/widgets/delete",
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"deleted": "runs"}
    assert service.summaries() == []


async def test_mutations_require_a_widget_id(tmp_path):
    service = make_service(tmp_path)

    response = await handler_for(service)._handle_widget_mutation(
        request_for("/api/webui/widgets/delete", {}),
        "/api/webui/widgets/delete",
    )

    assert response.status_code == 400


def test_widget_mutations_are_websocket_only():
    handler = handler_for()

    for action in ("webui.widgets.delete", "webui.widgets.refresh"):
        path = handler._webui_mutation_path(action, {})
        assert path == f"/api/webui/widgets/{action.rsplit('.', 1)[-1]}"
        assert handler._is_webui_mutation_path(path)


async def test_deleted_widgets_leave_the_home_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(home_widgets, "get_webui_dir", lambda: tmp_path)
    home_widgets.update_home_widget_layout(
        ["runs"], {"runs": {"visible": True, "collapsed": False}}
    )
    bus = MessageBus()
    bus.publish_outbound = AsyncMock()

    await publish_widget_change(bus, WidgetChange("runs", "deleted"))

    message = bus.publish_outbound.await_args.args[0]
    assert isinstance(message.event, WidgetChangedEvent)
    assert message.event.widget_id == "runs"
    assert message.event.kind == "deleted"
    assert home_widgets.read_home_widget_layout()["order"] == []


def test_widget_limits_and_ids_fit_the_home_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(home_widgets, "get_webui_dir", lambda: tmp_path)

    assert MAX_WIDGETS + 2 <= home_widgets._MAX_WIDGETS
    widget_id = "x" * 64
    layout = home_widgets.update_home_widget_layout(
        [widget_id], {widget_id: {"visible": True, "collapsed": False}}
    )
    assert layout["order"] == [widget_id]
