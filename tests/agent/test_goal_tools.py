"""Sustained-goal tool contracts for user-driven turns."""

from __future__ import annotations

import pytest

from yoreni.agent.goal_permission import GoalInputScope, goal_mutation_permission
from yoreni.agent.state import AgentStore
from yoreni.agent.tools.context import RequestContext, request_context
from yoreni.agent.tools.long_task import CreateGoalTool, UpdateGoalTool
from yoreni.bus.events import InboundMessage


def user_message(**overrides):
    fields = dict(
        channel="websocket", sender_id="owner", chat_id="chat", content="Ship the fix.",
    )
    fields.update(overrides)
    return InboundMessage(**fields)


def blocked_goal(store: AgentStore) -> None:
    store.update(0, {
        "goal_state": {
            "status": "blocked",
            "objective": "Deploy safely.",
            "recap": "Production access is required.",
        },
    })


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path)


async def test_create_goal_records_the_objective_on_a_user_turn(store):
    tool = CreateGoalTool(store)

    with request_context(RequestContext(channel="websocket", chat_id="chat")):
        with GoalInputScope(user_message()):
            result = await tool.execute(objective="Ship the fix.")

    assert "Goal recorded" in result
    state = store.identity().state["goal_state"]
    assert state["status"] == "active"
    assert state["objective"] == "Ship the fix."


async def test_create_goal_refuses_an_internal_turn(store):
    tool = CreateGoalTool(store)

    with goal_mutation_permission(True):
        with request_context(RequestContext(channel="dream", chat_id="chat")):
            with GoalInputScope(user_message(channel="dream", input_role="system")):
                result = await tool.execute(objective="Ship the fix.")

    assert "unavailable" in result
    assert "goal_state" not in store.identity().state


async def test_update_goal_resumes_a_blocked_goal(store):
    blocked_goal(store)
    tool = UpdateGoalTool(store)

    with request_context(RequestContext(channel="websocket", chat_id="chat")):
        with GoalInputScope(user_message(content="Access granted, continue.")):
            result = await tool.execute(action="resume")

    assert "resumed" in result
    state = store.identity().state["goal_state"]
    assert state["status"] == "active"
    assert state["recap"] == "Production access is required."


async def test_resume_requires_a_user_turn(store):
    blocked_goal(store)
    tool = UpdateGoalTool(store)

    with goal_mutation_permission(True):
        with request_context(RequestContext(channel="dream", chat_id="chat")):
            with GoalInputScope(user_message(channel="dream", input_role="system")):
                result = await tool.execute(action="resume")

    assert "unavailable" in result
    assert store.identity().state["goal_state"]["status"] == "blocked"
