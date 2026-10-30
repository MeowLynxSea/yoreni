import pytest

from yoreni.agent.goal_permission import (
    GoalInputScope,
    goal_mutation_allowed,
    goal_mutation_permission,
    revoke_goal_mutation_permission,
)
from yoreni.bus.events import InboundMessage


def user_message(**overrides):
    fields = dict(
        channel="websocket", sender_id="owner", chat_id="test", content="Ship the fix.",
    )
    fields.update(overrides)
    return InboundMessage(**fields)


def test_user_input_authorizes_goal_mutations():
    with GoalInputScope(user_message()):
        assert goal_mutation_allowed() is True
    assert goal_mutation_allowed() is False


@pytest.mark.parametrize("overrides", [
    {"channel": "dream", "input_role": "system"},
    {"channel": "system"},
])
def test_internal_input_cannot_authorize_goals(overrides):
    with goal_mutation_permission(True):
        with GoalInputScope(user_message(**overrides)):
            assert goal_mutation_allowed() is False
        assert goal_mutation_allowed() is True


def test_revoked_permission_stays_denied_for_the_rest_of_the_turn():
    with GoalInputScope(user_message()):
        revoke_goal_mutation_permission()
        assert goal_mutation_allowed() is False
    assert goal_mutation_allowed() is False
