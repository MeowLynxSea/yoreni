"""Tests for owner messages that arrive while a turn is already running."""

from __future__ import annotations

import pytest

from agent.conftest import make_loop
from yoreni.agent.injection import TurnInjections
from yoreni.agent.loop import _Interaction
from yoreni.agent.state import AgentInput
from yoreni.bus.events import INBOUND_META_TURN_ID, InboundMessage


def _active_turn(
    loop, chat_id: str = "yoreni", scope: str = "global",
) -> tuple[str, TurnInjections]:
    store = loop.agent.store
    run_id = store.admit(AgentInput(
        source="websocket", content="start", principal="owner", event_id="msg-1",
        channel="websocket", chat_id=chat_id,
    ))
    store.start(run_id, [])
    inbox = TurnInjections()
    loop._interactions[("websocket", chat_id, scope)] = _Interaction(
        run_id, "websocket", chat_id, inbox,
    )
    return run_id, inbox


def _message(
    content: str, *, event_id: str, chat_id: str = "yoreni", project_id: str | None = None,
) -> InboundMessage:
    metadata = {INBOUND_META_TURN_ID: event_id}
    if project_id is not None:
        metadata["project_id"] = project_id
    return InboundMessage(
        "websocket", "owner", chat_id, content,
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_message_breaks_into_the_running_turn(tmp_path) -> None:
    loop = make_loop(tmp_path)
    run_id, inbox = _active_turn(loop)

    accepted = await loop.accept_message(_message("also check the logs", event_id="msg-2"))

    assert accepted == run_id
    assert inbox.pending.qsize() == 1
    injected = await inbox.drain()
    assert "also check the logs" in injected[0]["content"]
    assert "Message ID: msg-2" in injected[0]["content"]
    entries = loop.agent.store.webui_history()[0]["entries"]
    assert [(entry["kind"], entry["payload"]["id"]) for entry in entries] == [("user", "msg-2")]


@pytest.mark.asyncio
async def test_message_for_another_chat_starts_its_own_run(tmp_path) -> None:
    loop = make_loop(tmp_path)
    run_id, inbox = _active_turn(loop)

    event = loop.agent.input_from_message(_message("other chat", event_id="msg-3", chat_id="elsewhere"))
    accepted = await loop._insert_message(event, _message("other chat", event_id="msg-3", chat_id="elsewhere"))

    assert accepted is None
    assert inbox.pending.empty()
    assert loop.agent.store.webui_history()[-1]["id"] == run_id


@pytest.mark.asyncio
async def test_project_message_does_not_join_the_main_turn(tmp_path) -> None:
    loop = make_loop(tmp_path)
    project = loop.projects.create(name="Yororen")
    run_id, inbox = _active_turn(loop)

    sent = _message("project work", event_id="msg-5", project_id=project.id)
    accepted = await loop._insert_message(loop.agent.input_from_message(sent), sent)

    assert accepted is None
    assert inbox.pending.empty()
    assert loop.agent.store.webui_history()[-1]["id"] == run_id


@pytest.mark.asyncio
async def test_message_that_missed_the_turn_is_not_kept(tmp_path) -> None:
    loop = make_loop(tmp_path)
    run_id, inbox = _active_turn(loop)
    inbox.close()

    event = loop.agent.input_from_message(_message("too late", event_id="msg-4"))
    accepted = await loop._insert_message(event, _message("too late", event_id="msg-4"))

    assert accepted is None
    entries = loop.agent.store.webui_history()[0]["entries"]
    assert [entry for entry in entries if entry["kind"] == "user"] == []
