import os

import pytest

from yoreni.agent.state import AgentInput, AgentStore
from yoreni.agent.tools.context import RequestContext, request_context
from yoreni.agent.tools.send_message import (
    LONG_MESSAGE_CHARS,
    SendMessageTool,
    capture_message_deliveries,
)
from yoreni.bus.events import OutboundMessage
from yoreni.config.paths import get_workspace_path


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
    assert result == "Error: provide text, media, or buttons"


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
async def test_send_message_records_media_deliveries() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)

    await tool.execute(
        text="image",
        channel="websocket",
        chat_id="chat-1",
        media=["/tmp/generated.png"],
    )

    assert sent[0].metadata == {"_record_channel_delivery": True}


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
async def test_send_message_resolves_relative_media_paths() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)

    await tool.execute(
        text="see attached",
        channel="telegram",
        chat_id="1",
        media=["output/image.png"],
    )

    expected = str(get_workspace_path() / "output/image.png")
    assert sent[0].media == [expected]


@pytest.mark.asyncio
async def test_send_message_resolves_relative_media_paths_from_active_workspace(tmp_path) -> None:
    sent, deliver = _collector()
    workspace = tmp_path / "workspace"
    tool = SendMessageTool(send_callback=deliver, workspace=workspace)

    await tool.execute(
        text="see attached",
        channel="telegram",
        chat_id="1",
        media=["output/image.png"],
    )

    assert sent[0].media == [str(workspace / "output/image.png")]


@pytest.mark.asyncio
async def test_send_message_rejects_outside_workspace_absolute_media_when_restricted(
    tmp_path,
) -> None:
    sent, deliver = _collector()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = SendMessageTool(send_callback=deliver, workspace=workspace, restrict_to_workspace=True)

    result = await tool.execute(
        text="see attached",
        channel="telegram",
        chat_id="1",
        media=[str(outside)],
    )

    assert result.startswith("Error: media path is not allowed:")
    assert "outside allowed directory" in result
    assert sent == []


@pytest.mark.asyncio
async def test_send_message_allows_workspace_absolute_media_when_restricted(tmp_path) -> None:
    sent, deliver = _collector()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = workspace / "image.png"
    image.write_text("image", encoding="utf-8")
    tool = SendMessageTool(send_callback=deliver, workspace=workspace, restrict_to_workspace=True)

    result = await tool.execute(
        text="see attached",
        channel="telegram",
        chat_id="1",
        media=[str(image)],
    )

    assert result.startswith("Message sent to telegram:1")
    assert "1 attachment(s) delivered." in result
    assert sent[0].media == [str(image.resolve())]


@pytest.mark.asyncio
async def test_send_message_passes_through_absolute_media_paths() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    abs_path = os.path.abspath(os.path.join(os.sep, "tmp", "abs_image.png"))

    await tool.execute(text="see attached", channel="telegram", chat_id="1", media=[abs_path])

    assert sent[0].media == [abs_path]


@pytest.mark.asyncio
async def test_send_message_passes_through_url_media_paths() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    url = "https://example.com/image.png"

    await tool.execute(text="see attached", channel="telegram", chat_id="1", media=[url])

    assert sent[0].media == [url]


@pytest.mark.asyncio
async def test_send_message_resolves_mixed_media_paths() -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    abs_path = os.path.abspath(os.path.join(os.sep, "tmp", "absolute.png"))

    await tool.execute(
        text="see attached",
        channel="telegram",
        chat_id="1",
        media=[
            "output/relative.png",
            abs_path,
            "https://example.com/url.png",
            "http://example.com/http.png",
        ],
    )

    expected_relative = str(get_workspace_path() / "output/relative.png")
    assert sent[0].media == [
        expected_relative,
        abs_path,
        "https://example.com/url.png",
        "http://example.com/http.png",
    ]


@pytest.mark.asyncio
async def test_send_message_rejects_wrong_explicit_ws_chat_id(tmp_path) -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    conv = "550e8400-e29b-41d4-a716-446655440000"
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    with request_context(RequestContext(channel="websocket", chat_id=conv, metadata={})):
        result = await tool.execute(
            text="see file",
            channel="websocket",
            chat_id="anon-deadbeefcafe",
            media=[str(f)],
        )
    assert result.startswith("Error: chat_id does not match")
    assert sent == []


@pytest.mark.asyncio
async def test_send_message_allows_ws_explicit_when_matches_context(tmp_path) -> None:
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    conv = "550e8400-e29b-41d4-a716-446655440000"
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    with request_context(RequestContext(channel="websocket", chat_id=conv, metadata={})):
        result = await tool.execute(
            text="see file",
            channel="websocket",
            chat_id=conv,
            media=[str(f)],
        )
    assert result.startswith("Message sent")
    assert sent[0].chat_id == conv


@pytest.mark.asyncio
async def test_send_message_cli_context_may_target_other_ws_chat(tmp_path) -> None:
    """Cron / CLI handlers keep non-websocket defaults; an explicit websocket uuid remains valid."""
    sent, deliver = _collector()
    tool = SendMessageTool(send_callback=deliver)
    target = "550e8400-e29b-41d4-a716-446655440000"
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    with request_context(RequestContext(channel="cli", chat_id="direct", metadata={})):
        result = await tool.execute(
            text="ping",
            channel="websocket",
            chat_id=target,
            media=[str(f)],
        )
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
