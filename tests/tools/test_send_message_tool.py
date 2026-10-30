import pytest

from yoreni.agent.state import AgentInput, AgentStore
from yoreni.agent.tools.chat_delivery import capture_message_deliveries
from yoreni.agent.tools.context import RequestContext, request_context
from yoreni.agent.tools.send_message import LONG_MESSAGE_CHARS, SendMessageTool
from yoreni.bus.events import OutboundMessage


def _collector():
    sent = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    return sent, _send


@pytest.mark.asyncio
async def test_send_message_returns_error_when_no_target_context() -> None:
    tool = SendMessageTool()
    result = await tool.execute(text="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
async def test_send_message_requires_some_payload() -> None:
    tool = SendMessageTool()
    result = await tool.execute(text="   ", channel="telegram", chat_id="1")
    assert result == "Error: provide text or buttons"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not a list",
        [["ok"], "row-not-a-list"],
        [["ok", 42]],
        [[None]],
    ],
)
async def test_send_message_rejects_malformed_buttons(bad) -> None:
    """Buttons must be list[list[str]]; validating here keeps a malformed
    payload visible instead of letting the channel layer drop the frame."""
    tool = SendMessageTool()
    result = await tool.execute(
        text="hi",
        channel="telegram",
        chat_id="1",
        buttons=bad,
    )
    assert result == "Error: buttons must be a list of list of strings"


@pytest.mark.asyncio
async def test_send_message_suppresses_delivery_when_active() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)

    token = tool.set_suppress_delivery(True)
    try:
        result = await tool.execute(text="all clear", channel="telegram", chat_id="1")
    finally:
        tool.reset_suppress_delivery(token)
    assert sent == []
    assert "not delivered" in result

    await tool.execute(text="real", channel="telegram", chat_id="1")
    assert len(sent) == 1
    assert sent[0].content == "real"


@pytest.mark.asyncio
async def test_send_message_inherits_metadata_for_same_target() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    slack_meta = {"slack": {"thread_ts": "111.222", "channel_type": "channel"}}

    with request_context(RequestContext(channel="slack", chat_id="C123", metadata=slack_meta)):
        await tool.execute(text="thread reply")

    assert sent[0].metadata == slack_meta


@pytest.mark.asyncio
async def test_send_message_clears_metadata_when_context_has_none() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    rich_context = RequestContext(
        channel="slack",
        chat_id="C123",
        metadata={"slack": {"thread_ts": "111.222", "channel_type": "channel"}},
    )
    with request_context(rich_context):
        await tool.execute(text="thread reply")
    sent.clear()

    with request_context(RequestContext(channel="slack", chat_id="C123", metadata={})):
        await tool.execute(text="plain reply")

    assert sent[0].metadata == {}


@pytest.mark.asyncio
async def test_send_message_does_not_inherit_metadata_for_cross_target() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    with request_context(
        RequestContext(
            channel="slack",
            chat_id="C123",
            metadata={"slack": {"thread_ts": "111.222", "channel_type": "channel"}},
        ),
    ):
        await tool.execute(text="channel reply", channel="slack", chat_id="C999")

    assert sent[0].metadata == {}


@pytest.mark.asyncio
async def test_send_message_rejects_wrong_explicit_ws_chat_id() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    conv = "550e8400-e29b-41d4-a716-446655440000"
    with request_context(RequestContext(channel="websocket", chat_id=conv, metadata={})):
        result = await tool.execute(
            text="hello",
            channel="websocket",
            chat_id="anon-deadbeefcafe",
        )
    assert result.startswith("Error: chat_id does not match")
    assert sent == []


@pytest.mark.asyncio
async def test_send_message_allows_ws_explicit_when_matches_context() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    conv = "550e8400-e29b-41d4-a716-446655440000"
    with request_context(RequestContext(channel="websocket", chat_id=conv, metadata={})):
        result = await tool.execute(text="hello", channel="websocket", chat_id=conv)
    assert result.startswith("Message sent")
    assert sent[0].chat_id == conv


@pytest.mark.asyncio
async def test_send_message_cli_context_may_target_other_ws_chat() -> None:
    """Cron / CLI handlers keep non-websocket defaults; an explicit websocket uuid remains valid."""
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    target = "550e8400-e29b-41d4-a716-446655440000"
    with request_context(RequestContext(channel="cli", chat_id="direct", metadata={})):
        result = await tool.execute(text="ping", channel="websocket", chat_id=target)
    assert result.startswith("Message sent")
    assert sent[0].channel == "websocket"
    assert sent[0].chat_id == target


@pytest.mark.asyncio
async def test_send_message_rejects_cross_channel_send_with_unknown_chat(tmp_path) -> None:
    """Cross-channel sends must not fall back to the current chat id."""
    store = AgentStore(tmp_path)
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver, store=store)

    with request_context(RequestContext(channel="websocket", chat_id="yoreni", metadata={})):
        result = await tool.execute(text="ping", channel="telegram")

    assert result == "Error: no chat known for channel 'telegram'. Pass chat_id explicitly."
    assert sent == []


@pytest.mark.asyncio
async def test_send_message_cross_channel_send_uses_last_owner_chat(tmp_path) -> None:
    """The chat the owner last used on that channel is the one to answer."""
    store = AgentStore(tmp_path)
    run_id = store.admit(AgentInput(
        source="telegram", content="hi", principal="owner",
        channel="telegram", chat_id="tg-42",
    ))
    store.start(run_id, [])
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver, store=store)

    with request_context(RequestContext(channel="websocket", chat_id="yoreni", metadata={})):
        result = await tool.execute(text="reminder", channel="telegram")

    assert result.startswith("Message sent")
    assert (sent[0].channel, sent[0].chat_id) == ("telegram", "tg-42")


@pytest.mark.asyncio
async def test_send_message_persists_one_durable_row_per_message(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = store.admit(AgentInput(
        source="websocket", content="hello", principal="owner",
        channel="websocket", chat_id="yoreni",
    ))
    store.start(run_id, [])
    _, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver, store=store)

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, metadata={},
    )):
        first = await tool.execute(text="one")
        second = await tool.execute(text="two")

    entries = store.webui_history()[-1]["entries"]
    assert [entry["kind"] for entry in entries] == ["message", "message"]
    assert [entry["payload"]["content"] for entry in entries] == ["one", "two"]
    assert "rich" not in entries[0]["payload"]
    assert entries[0]["payload"]["id"] in first
    assert entries[1]["payload"]["id"] in second


@pytest.mark.asyncio
async def test_send_message_warns_about_overlong_text() -> None:
    _, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)

    result = await tool.execute(
        text="x" * (LONG_MESSAGE_CHARS + 1), channel="telegram", chat_id="1",
    )

    assert "trimmed bubble" in result
    assert "shorter messages" in result


@pytest.mark.asyncio
async def test_send_message_validates_reply_target_and_quote(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = store.admit(AgentInput(
        source="websocket", content="original", principal="owner", event_id="msg-1",
        channel="websocket", chat_id="yoreni",
    ))
    store.start(run_id, [])
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver, store=store)

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, metadata={},
    )):
        unknown = await tool.execute(text="hi", reply_to="missing")
        wrong_quote = await tool.execute(text="hi", reply_to="msg-1", quote="not there")
        ok = await tool.execute(text="hi", reply_to="msg-1", quote="original")

    assert unknown.startswith("Error: unknown reply_to message ID")
    assert wrong_quote.startswith("Error: quote must be an exact excerpt")
    assert ok.startswith("Message sent")
    assert sent[-1].event.quote == "original"


@pytest.mark.asyncio
async def test_send_message_counts_sends_for_turn_delivery() -> None:
    _, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)

    with capture_message_deliveries() as sent:
        await tool.execute(text="hi", channel="telegram", chat_id="1")
        await tool.execute(text="again", channel="telegram", chat_id="1")

    assert sent.count == 2
    assert sent.targets == {("telegram", "1")}
