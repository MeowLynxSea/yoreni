import pytest

from yoreni.agent.injection import TurnInjections
from yoreni.agent.state import AgentInput, AgentStore
from yoreni.agent.tools.context import RequestContext, request_context
from yoreni.agent.tools.react import ReactTool
from yoreni.bus.events import OutboundMessage


def _collector():
    sent = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    return sent, _send


def _run(store: AgentStore, event_id: str = "msg-1") -> str:
    run_id = store.admit(AgentInput(
        source="websocket", content="hello", principal="owner", event_id=event_id,
        channel="websocket", chat_id="yoreni",
    ))
    store.start(run_id, [])
    return run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", ["", "   ", "ok", "two words", "🙂🙂 extra"])
async def test_react_rejects_text_instead_of_one_emoji(emoji: str) -> None:
    tool = ReactTool()
    result = await tool.execute(emoji=emoji)
    assert result == "Error: emoji must be a single emoji without words"


@pytest.mark.asyncio
@pytest.mark.parametrize("decline", ["NONE", "none", " None "])
async def test_react_can_decline_when_no_emoji_fits(decline: str, tmp_path) -> None:
    """Declining must leave no reaction row and send nothing."""
    store = AgentStore(tmp_path)
    run_id = _run(store)
    sent, deliver = _collector()
    tool = ReactTool(send_callback=deliver, store=store)

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, message_id="msg-1",
    )):
        result = await tool.execute(emoji=decline)

    assert result == "No reaction sent."
    assert sent == []
    assert store.webui_history()[-1]["entries"] == []


@pytest.mark.asyncio
async def test_react_requires_a_target_message() -> None:
    _, deliver = _collector()
    tool = ReactTool(send_callback=deliver)
    result = await tool.execute(emoji="👀")
    assert result == "Error: no message to react to. Pass message_id explicitly."


@pytest.mark.asyncio
async def test_react_refuses_unknown_message_ids(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = _run(store)
    _, deliver = _collector()
    tool = ReactTool(send_callback=deliver, store=store)

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, message_id="msg-1",
    )):
        result = await tool.execute(emoji="🤔", message_id="does-not-exist")

    assert result == (
        "Error: unknown message ID 'does-not-exist'. Use an ID from the "
        "recent-messages list, or omit message_id."
    )


@pytest.mark.asyncio
async def test_react_targets_the_arriving_message_and_persists(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = _run(store)
    sent, deliver = _collector()
    tool = ReactTool(send_callback=deliver, store=store)

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, message_id="msg-1",
    )):
        result = await tool.execute(emoji="😊")

    assert result == "Reacted 😊 to message msg-1."
    assert len(sent) == 1
    assert sent[0].channel == "websocket"
    assert sent[0].event is not None
    assert sent[0].event.target == "msg-1"
    assert sent[0].event.emoji == "😊"

    entries = store.webui_history()[0]["entries"]
    assert [entry["kind"] for entry in entries] == ["reaction"]
    assert entries[0]["payload"]["target"] == "msg-1"
    assert entries[0]["payload"]["emoji"] == "😊"


@pytest.mark.asyncio
async def test_react_defaults_to_the_message_that_broke_in(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = _run(store)
    store.append_entry(run_id, "user", {"id": "msg-2", "content": "and this too"})
    sent, deliver = _collector()
    tool = ReactTool(send_callback=deliver, store=store)
    inbox = TurnInjections()
    inbox.try_push(AgentInput(
        source="websocket", content="and this too", principal="owner", event_id="msg-2",
        channel="websocket", chat_id="yoreni",
    ))
    await inbox.drain()

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, message_id="msg-1",
    )):
        result = await tool.execute(emoji="👀")

    assert result == "Reacted 👀 to message msg-2."
    assert sent[0].event.target == "msg-2"


@pytest.mark.asyncio
async def test_react_can_target_an_older_message(tmp_path) -> None:
    store = AgentStore(tmp_path)
    older = store.admit(AgentInput(
        source="websocket", content="an earlier thought", principal="owner", event_id="msg-0",
        channel="websocket", chat_id="yoreni",
    ))
    store.start(older, [])
    run_id = _run(store)
    sent, deliver = _collector()
    tool = ReactTool(send_callback=deliver, store=store)

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, message_id="msg-1",
    )):
        result = await tool.execute(emoji="👍", message_id="msg-0")

    assert result == "Reacted 👍 to message msg-0."
    assert sent[0].event.target == "msg-0"
