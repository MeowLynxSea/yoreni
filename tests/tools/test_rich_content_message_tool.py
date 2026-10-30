import os

import pytest

from yoreni.agent.state import AgentInput, AgentStore
from yoreni.agent.tools.chat_delivery import capture_message_deliveries
from yoreni.agent.tools.context import RequestContext, request_context
from yoreni.agent.tools.rich_content_message import RichContentMessageTool
from yoreni.bus.events import OutboundMessage
from yoreni.config.paths import get_workspace_path
from yoreni.utils.link_preview import LinkPreview


def _collector():
    sent = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    return sent, _send


@pytest.mark.asyncio
async def test_rich_content_requires_at_least_one_file() -> None:
    tool = RichContentMessageTool()
    result = await tool.execute(media=[], channel="telegram", chat_id="1")
    assert result == "Error: provide at least one file in media"


@pytest.mark.asyncio
async def test_rich_content_delivers_a_card_event() -> None:
    sent, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver)
    image = os.path.abspath(os.path.join(os.sep, "tmp", "chart.png"))

    result = await tool.execute(media=[image], channel="telegram", chat_id="1")

    assert result.startswith("Sent 1 item(s) to telegram:1")
    assert sent[0].media == [image]
    assert sent[0].event.rich is True
    assert sent[0].content == ""
    assert sent[0].metadata == {"_record_channel_delivery": True}


@pytest.mark.asyncio
async def test_rich_content_resolves_relative_media_paths() -> None:
    sent, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver)

    await tool.execute(media=["output/image.png"], channel="telegram", chat_id="1")

    assert sent[0].media == [str(get_workspace_path() / "output/image.png")]


@pytest.mark.asyncio
async def test_rich_content_resolves_relative_media_paths_from_active_workspace(tmp_path) -> None:
    sent, deliver = _collector()
    workspace = tmp_path / "workspace"
    tool = RichContentMessageTool(send_callback=deliver, workspace=workspace)

    await tool.execute(media=["output/image.png"], channel="telegram", chat_id="1")

    assert sent[0].media == [str(workspace / "output/image.png")]


@pytest.mark.asyncio
async def test_rich_content_rejects_outside_workspace_absolute_media_when_restricted(
    tmp_path,
) -> None:
    sent, deliver = _collector()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = RichContentMessageTool(
        send_callback=deliver, workspace=workspace, restrict_to_workspace=True,
    )

    result = await tool.execute(media=[str(outside)], channel="telegram", chat_id="1")

    assert result.startswith("Error: media path is not allowed:")
    assert "outside allowed directory" in result
    assert sent == []


@pytest.mark.asyncio
async def test_rich_content_allows_workspace_absolute_media_when_restricted(tmp_path) -> None:
    sent, deliver = _collector()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = workspace / "image.png"
    image.write_text("image", encoding="utf-8")
    tool = RichContentMessageTool(
        send_callback=deliver, workspace=workspace, restrict_to_workspace=True,
    )

    result = await tool.execute(media=[str(image)], channel="telegram", chat_id="1")

    assert result.startswith("Sent 1 item(s) to telegram:1")
    assert sent[0].media == [str(image.resolve())]


@pytest.mark.asyncio
async def test_rich_content_resolves_mixed_media_paths() -> None:
    sent, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver)
    abs_path = os.path.abspath(os.path.join(os.sep, "tmp", "absolute.png"))

    await tool.execute(
        media=[
            "output/relative.png",
            abs_path,
            "https://example.com/url.png",
            "http://example.com/http.png",
        ],
        channel="telegram",
        chat_id="1",
    )

    assert sent[0].media == [
        str(get_workspace_path() / "output/relative.png"),
        abs_path,
        "https://example.com/url.png",
        "http://example.com/http.png",
    ]


@pytest.mark.asyncio
async def test_rich_content_persists_a_rich_row(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = store.admit(AgentInput(
        source="websocket", content="hello", principal="owner",
        channel="websocket", chat_id="yoreni",
    ))
    store.start(run_id, [])
    _, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver, store=store)
    image = os.path.abspath(os.path.join(os.sep, "tmp", "chart.png"))

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, metadata={},
    )):
        result = await tool.execute(media=[image], text="quarterly numbers")

    entry = store.webui_history()[-1]["entries"][0]
    assert entry["kind"] == "message"
    assert entry["payload"]["rich"] is True
    assert entry["payload"]["media"] == [image]
    assert entry["payload"]["content"] == "quarterly numbers"
    assert entry["payload"]["id"] in result


@pytest.mark.asyncio
async def test_rich_content_validates_the_reply_target(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = store.admit(AgentInput(
        source="websocket", content="original", principal="owner", event_id="msg-1",
        channel="websocket", chat_id="yoreni",
    ))
    store.start(run_id, [])
    sent, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver, store=store)
    image = os.path.abspath(os.path.join(os.sep, "tmp", "chart.png"))

    with request_context(RequestContext(
        channel="websocket", chat_id="yoreni", turn_id=run_id, metadata={},
    )):
        unknown = await tool.execute(media=[image], reply_to="missing")
        wrong_quote = await tool.execute(media=[image], reply_to="msg-1", quote="not there")
        ok = await tool.execute(media=[image], reply_to="msg-1", quote="original")

    assert unknown.startswith("Error: unknown reply_to message ID")
    assert wrong_quote.startswith("Error: quote must be an exact excerpt")
    assert ok.startswith("Sent 1 item(s)")
    assert sent[-1].event.reply_to == "msg-1"
    assert sent[-1].event.quote == "original"


@pytest.mark.asyncio
async def test_rich_content_suppresses_delivery_when_active() -> None:
    sent, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver)
    image = os.path.abspath(os.path.join(os.sep, "tmp", "chart.png"))

    token = tool.set_suppress_delivery(True)
    try:
        result = await tool.execute(media=[image], channel="telegram", chat_id="1")
    finally:
        tool.reset_suppress_delivery(token)

    assert sent == []
    assert "not delivered" in result


@pytest.mark.asyncio
async def test_rich_content_counts_sends_for_turn_delivery() -> None:
    _, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver)
    image = os.path.abspath(os.path.join(os.sep, "tmp", "chart.png"))

    with capture_message_deliveries() as sent:
        await tool.execute(media=[image], channel="telegram", chat_id="1")
        await tool.execute(media=[image], channel="telegram", chat_id="1")

    assert sent.count == 2
    assert sent.targets == {("telegram", "1")}


@pytest.mark.asyncio
async def test_rich_content_previews_links_as_cards(monkeypatch) -> None:
    sent, deliver = _collector()
    tool = RichContentMessageTool(send_callback=deliver)

    async def _preview(url: str) -> LinkPreview:
        return LinkPreview(url=url, title="Example", description="A page", site="example.com")

    monkeypatch.setattr("yoreni.agent.tools.chat_delivery.fetch_link_preview", _preview)

    result = await tool.execute(media=["https://example.com/a"], channel="telegram", chat_id="1")

    assert result.startswith("Sent 1 item(s)")
    assert sent[0].media == ["https://example.com/a"]
    assert sent[0].event.rich is True
    assert sent[0].event.links == (LinkPreview(
        url="https://example.com/a", title="Example", description="A page", site="example.com",
    ),)
