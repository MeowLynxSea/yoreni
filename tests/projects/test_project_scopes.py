"""Conversation isolation and admission for scoped conversations."""

from __future__ import annotations

from uuid import uuid4

import pytest
from project_helpers import ProjectEnv

from yoreni.agent.state import AgentInput
from yoreni.projects.scope import GLOBAL_SCOPE


def _history_texts(env: ProjectEnv, scope: str) -> list[str]:
    return [run["content"] for run in env.store.webui_history(scope=scope)]


def _recent_texts(env: ProjectEnv, scope: str) -> set[str]:
    return {str(message["content"]) for message in env.store.recent(scope=scope)}


def _row_texts(env: ProjectEnv, scope: str) -> set[str]:
    return {row["text"] for row in env.store.conversation_rows(scope=scope)}


def test_each_conversation_keeps_its_own_history(env: ProjectEnv):
    alpha = env.create("Alpha")
    beta = env.create("Beta")
    main_message = uuid4().hex
    alpha_message = uuid4().hex
    beta_message = uuid4().hex
    env.turn("main chat question", message_id=main_message, answer="main answer")
    env.turn("alpha question", scope=alpha.scope, message_id=alpha_message, answer="alpha answer")
    env.turn("beta question", scope=beta.scope, message_id=beta_message, answer="beta answer")

    assert _history_texts(env, alpha.scope) == ["alpha question"]
    assert _history_texts(env, beta.scope) == ["beta question"]
    assert _history_texts(env, GLOBAL_SCOPE) == ["main chat question"]
    assert _recent_texts(env, alpha.scope) == {"alpha question", "alpha answer"}
    assert _recent_texts(env, beta.scope) == {"beta question", "beta answer"}
    assert _recent_texts(env, GLOBAL_SCOPE) == {"main chat question", "main answer"}
    assert _row_texts(env, alpha.scope) == {"alpha question", "alpha answer"}
    assert _row_texts(env, beta.scope) == {"beta question", "beta answer"}
    assert _row_texts(env, GLOBAL_SCOPE) == {"main chat question", "main answer"}

    assert env.store.resolve_message_reference(alpha_message, scope=alpha.scope) == {
        "role": "user", "content": "alpha question",
    }
    assert env.store.resolve_message_reference(alpha_message, scope=beta.scope) is None
    assert env.store.resolve_message_reference(alpha_message) is None
    assert env.store.resolve_message_reference(main_message) == {
        "role": "user", "content": "main chat question",
    }


def test_admission_rejects_an_unknown_project_scope(env: ProjectEnv):
    scope = "project:" + "ab" * 16
    event_id = uuid4().hex

    with pytest.raises(ValueError, match="unknown project"):
        env.store.admit(AgentInput(
            source="webui", content="hello", principal="owner",
            event_id=event_id, scope=scope,
        ))

    assert env.store.find_event("webui", event_id) is None
    assert env.store.scoped_run_ids(scope) == []


def test_event_and_run_carry_the_same_project_scope(env: ProjectEnv):
    project = env.create("Scoped")
    event_id = uuid4().hex

    run_id = env.store.admit(AgentInput(
        source="webui", content="hello", principal="owner",
        event_id=event_id, scope=project.scope,
    ))

    run = env.store.read_run(run_id)
    assert run["scope"] == project.scope
    assert run["payload"]["scope"] == project.scope
    assert run["payload"]["event_id"] == event_id
    assert env.store.find_event("webui", event_id)["scope"] == project.scope
