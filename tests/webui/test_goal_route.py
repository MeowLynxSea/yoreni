"""Goal snapshot route for the Home widgets."""

import json
import time

from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.agent.state import AgentStore
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ws_http import GatewayHTTPHandler


def handler_for(workspace):
    handler = object.__new__(GatewayHTTPHandler)
    handler.skills_workspace_path = workspace
    tokens = GatewayTokenStore(max_tokens=4)
    tokens.api_tokens["fixture-token"] = time.monotonic() + 60
    handler.tokens = tokens
    return handler


async def test_goal_route_reports_the_open_goal(tmp_path):
    AgentStore(tmp_path).update(0, {
        "goal_state": {
            "status": "active",
            "objective": "Ship the fix.",
            "ui_summary": "ship",
        },
    })
    request = Request("/api/webui/goal", Headers({
        "Authorization": "Bearer fixture-token",
    }))

    response = await handler_for(tmp_path)._handle_webui_goal(request)

    assert response.status_code == 200
    assert json.loads(response.body) == {"goal": {
        "active": True,
        "status": "active",
        "ui_summary": "ship",
        "objective": "Ship the fix.",
    }}


async def test_goal_route_requires_a_token(tmp_path):
    response = await handler_for(tmp_path)._handle_webui_goal(
        Request("/api/webui/goal", Headers()),
    )

    assert response.status_code == 401
