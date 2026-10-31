from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yoreni.bus.events import OutboundMessage
from yoreni.bus.outbound_events import AgentMessageEvent, ReactionEvent, UserInputEvent
from yoreni.utils.link_preview import LinkPreview
from yoreni.webui.outbound_delivery import WebUIOutboundDelivery


class _Transport:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def webui_recipients(self, audience: object) -> tuple[object, ...]:
        return (object(),)

    async def webui_send_raw(self, connection: object, raw: str, *, label: str = "") -> None:
        self.frames.append(json.loads(raw))


class _Media:
    def rewrite_local_markdown_images(self, text: str) -> str:
        return text

    def sign_or_stage_media_path(self, path: Path) -> dict[str, str] | None:
        return {"url": f"/api/media/{path.name}", "name": path.name}


def _delivery() -> tuple[WebUIOutboundDelivery, _Transport]:
    transport = _Transport()
    return WebUIOutboundDelivery(transport, media=_Media()), transport  # type: ignore[arg-type]


def _message(event: Any, *, media: list[str] | None = None) -> OutboundMessage:
    return OutboundMessage(
        channel="websocket",
        chat_id="yoreni",
        content=getattr(event, "content", ""),
        media=list(media or []),
        event=event,
    )


@pytest.mark.asyncio
async def test_delivered_message_keeps_its_row_and_reply_context() -> None:
    delivery, transport = _delivery()
    event = AgentMessageEvent(
        message_id="m1",
        content="done",
        created_at_ms=1,
        reply_to="u1",
        quote="the original",
    )

    await delivery.send(_message(event))

    assert transport.frames == [{
        "event": "message",
        "chat_id": "yoreni",
        "id": "m1",
        "text": "done",
        "reply_to": "u1",
        "quote": "the original",
    }]


@pytest.mark.asyncio
async def test_delivered_message_signs_each_attachment_once() -> None:
    """The tool puts the same paths on the Event and on the OutboundMessage."""
    delivery, transport = _delivery()
    event = AgentMessageEvent(
        message_id="m1",
        content="here",
        created_at_ms=1,
        media=("/tmp/a.png",),
    )

    await delivery.send(_message(event, media=["/tmp/a.png"]))

    assert transport.frames[0]["media_urls"] == [
        {"url": "/api/media/a.png", "name": "a.png"},
    ]


@pytest.mark.asyncio
async def test_a_rich_message_is_marked_as_a_card() -> None:
    delivery, transport = _delivery()
    event = AgentMessageEvent(
        message_id="m2",
        content="",
        created_at_ms=1,
        media=("/tmp/a.png",),
        rich=True,
    )

    await delivery.send(_message(event, media=["/tmp/a.png"]))

    assert transport.frames[0]["rich"] is True
    assert transport.frames[0]["media_urls"] == [
        {"url": "/api/media/a.png", "name": "a.png"},
    ]


@pytest.mark.asyncio
async def test_a_link_arrives_as_a_card_instead_of_a_bare_url() -> None:
    delivery, transport = _delivery()
    link = LinkPreview(url="https://example.com/a", title="Example", description="A page", site="example.com")
    event = AgentMessageEvent(
        message_id="m4", content="https://example.com/a", created_at_ms=1,
        media=("https://example.com/a",), rich=True, links=(link,),
    )

    await delivery.send(_message(event, media=["https://example.com/a"]))

    frame = transport.frames[0]
    assert frame["text"] == ""
    assert frame["media_urls"] == [{
        "kind": "link",
        "url": "https://example.com/a",
        "name": "Example",
        "title": "Example",
        "description": "A page",
        "site": "example.com",
    }]


@pytest.mark.asyncio
async def test_a_url_without_a_preview_still_becomes_a_card() -> None:
    delivery, transport = _delivery()
    event = AgentMessageEvent(
        message_id="m5", content="", created_at_ms=1, media=("https://www.example.com/a",),
    )

    await delivery.send(_message(event))

    assert transport.frames[0]["media_urls"] == [{
        "kind": "link",
        "url": "https://www.example.com/a",
        "name": "example.com",
        "title": "example.com",
        "description": "",
        "site": "example.com",
    }]


@pytest.mark.asyncio
async def test_reaction_frame_targets_the_reacted_row() -> None:
    delivery, transport = _delivery()

    await delivery.send(_message(ReactionEvent(message_id="r1", emoji="👍", created_at_ms=2, target="u1")))

    assert transport.frames == [{
        "event": "reaction",
        "id": "r1",
        "target": "u1",
        "emoji": "👍",
        "created_at_ms": 2,
    }]


@pytest.mark.asyncio
async def test_inserted_user_message_is_pushed_with_its_id() -> None:
    delivery, transport = _delivery()

    await delivery.send(_message(UserInputEvent(
        content="one more thing", created_at_ms=3, provenance={}, message_id="u2",
    )))

    assert transport.frames == [{
        "event": "user_message",
        "text": "one more thing",
        "created_at_ms": 3,
        "message_id": "u2",
    }]


@pytest.mark.asyncio
async def test_user_message_carries_the_attachments_it_was_sent_with() -> None:
    delivery, transport = _delivery()

    await delivery.send(_message(UserInputEvent(
        content="", created_at_ms=4, provenance={}, message_id="u3",
        media=("chart.png", "report.pdf"),
    )))

    assert transport.frames[0]["media_urls"] == [
        {"url": "/api/media/chart.png", "name": "chart.png"},
        {"url": "/api/media/report.pdf", "name": "report.pdf"},
    ]
