"""Project live Run output for authenticated personal clients."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from yoreni.agent.state import PERSONAL_CHAT_ID
from yoreni.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
from yoreni.bus.outbound_events import (
    AgentMessageEvent,
    GoalStateSyncEvent,
    GoalStatusEvent,
    ProgressEvent,
    ReactionEvent,
    RetryWaitEvent,
    RuntimeModelUpdatedEvent,
    StreamedResponseEvent,
    TurnEndEvent,
    TurnModelUpdatedEvent,
    UserInputEvent,
    WidgetChangedEvent,
)
from yoreni.bus.queue import MessageBus
from yoreni.pairing.events import PairingChanged
from yoreni.webui.media_gateway import WebUIMediaGateway
from yoreni.webui.metadata import WEBUI_TURN_METADATA_KEY
from yoreni.webui.outbound_wire import (
    encode_turn_end,
    media_attachments,
    message_text,
    project_notification,
    project_tool_events,
)

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection


@dataclass(frozen=True)
class ChatAudience:
    """An output destination, without a conversation lifecycle."""

    chat_id: str


WebUIAudience: TypeAlias = ChatAudience | Literal["all_connections", "authenticated_webui"]


class WebUIOutboundTransport(Protocol):
    def webui_recipients(self, audience: WebUIAudience) -> tuple[ServerConnection, ...]: ...
    async def webui_send_raw(self, connection: ServerConnection, raw: str, *, label: str = "") -> None: ...


class WebUIOutboundDelivery:
    def __init__(self, transport: WebUIOutboundTransport, *, media: WebUIMediaGateway):
        self._transport = transport
        self._media = media

    def bind_pairing(self, bus: MessageBus, *, channel: str) -> Callable[[], None]:
        async def changed(event: PairingChanged) -> None:
            await bus.publish_event(event, channel=channel, chat_id=PERSONAL_CHAT_ID)
        return bus.subscribe(changed, PairingChanged)

    async def _send(self, payload: Mapping[str, object], metadata: dict[str, Any] | None = None) -> None:
        body = dict(payload)
        if metadata and isinstance(metadata.get(WEBUI_TURN_METADATA_KEY), str):
            body["turn_id"] = metadata[WEBUI_TURN_METADATA_KEY]
        if metadata and isinstance(metadata.get("run_id"), str):
            body["run_id"] = metadata["run_id"]
        raw = json.dumps(body, ensure_ascii=False)
        for connection in self._transport.webui_recipients("authenticated_webui"):
            await self._transport.webui_send_raw(connection, raw)

    async def send(self, msg: OutboundMessage) -> None:
        event = msg.event
        if isinstance(event, RetryWaitEvent | StreamedResponseEvent):
            return
        if isinstance(event, PairingChanged):
            await self._send({"event": "pairing_changed"})
            return
        if isinstance(event, WidgetChangedEvent):
            await self._send({
                "event": "widget_changed",
                "widget_id": event.widget_id,
                "kind": event.kind,
            })
            return
        if isinstance(event, RuntimeModelUpdatedEvent | TurnModelUpdatedEvent):
            await self._send({
                "event": "runtime_model_updated" if isinstance(event, RuntimeModelUpdatedEvent) else "turn_model_updated",
                "chat_id": PERSONAL_CHAT_ID, "model": event.model, "model_preset": event.model_preset,
            }, msg.metadata)
            return
        if isinstance(event, TurnEndEvent):
            await self._send(encode_turn_end(PERSONAL_CHAT_ID, event, msg.metadata), msg.metadata)
            return
        if isinstance(event, GoalStateSyncEvent):
            await self._send({"event": "goal_state", "goal_state": event.goal_state}, msg.metadata)
            return
        if isinstance(event, GoalStatusEvent):
            await self._send({"event": "goal_status", "status": event.status, "started_at": event.started_at}, msg.metadata)
            return
        if isinstance(event, UserInputEvent):
            user_payload: dict[str, Any] = {
                "event": "user_message", "text": event.content,
                "created_at_ms": event.created_at_ms,
            }
            if event.message_id:
                user_payload["message_id"] = event.message_id
            await self._send(user_payload, msg.metadata)
            return
        if isinstance(event, AgentMessageEvent):
            await self._send(self._message_payload(event, msg), msg.metadata)
            return
        if isinstance(event, ReactionEvent):
            await self._send({
                "event": "reaction",
                "id": event.message_id,
                "target": event.target,
                "emoji": event.emoji,
                "created_at_ms": event.created_at_ms,
            }, msg.metadata)
            return
        notification = project_notification(PERSONAL_CHAT_ID, event, msg.metadata)
        if notification is not None:
            await self._send(notification.payload, msg.metadata)
            return
        payload: dict[str, Any] = {
            "event": "message", "text": self._media.rewrite_local_markdown_images(msg.content),
            "chat_id": PERSONAL_CHAT_ID,
        }
        if msg.media:
            payload["media_urls"] = [signed for entry in msg.media
                                     if (signed := self._media.sign_or_stage_media_path(Path(entry))) is not None]
        if isinstance(event, ProgressEvent):
            payload["kind"] = "tool_hint" if event.tool_hint else "progress"
            if event.tool_events:
                payload["tool_events"] = project_tool_events(event.tool_events)
            if event.file_edit_events:
                payload["file_edit_events"] = event.file_edit_events
        if OUTBOUND_META_AGENT_UI in msg.metadata:
            payload["agent_ui"] = msg.metadata[OUTBOUND_META_AGENT_UI]
        await self._send(payload, msg.metadata)

    def _message_payload(self, event: AgentMessageEvent, msg: OutboundMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": PERSONAL_CHAT_ID,
            "id": event.message_id,
            "text": self._media.rewrite_local_markdown_images(
                message_text(event.content, event.links),
            ),
        }
        # The tool puts the same files on the Event and the OutboundMessage.
        media = list(dict.fromkeys((*event.media, *msg.media)))
        attachments = media_attachments(
            media,
            sign_media=lambda entry: self._media.sign_or_stage_media_path(Path(entry)),
            links=event.links,
        )
        if attachments:
            payload["media_urls"] = attachments
        if event.reply_to:
            payload["reply_to"] = event.reply_to
        if event.quote:
            payload["quote"] = event.quote
        if event.rich:
            payload["rich"] = True
        if msg.buttons:
            payload["buttons"] = msg.buttons
        return payload

    async def send_reasoning_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None, *, stream_id: str | None = None) -> None:
        await self._send({"event": "reasoning_delta", "text": delta, "stream_id": stream_id}, metadata)

    async def send_reasoning_end(self, chat_id: str, metadata: dict[str, Any] | None = None, *, stream_id: str | None = None) -> None:
        await self._send({"event": "reasoning_end", "stream_id": stream_id}, metadata)

    async def send_file_edit_events(self, chat_id: str, edits: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> None:
        await self._send({"event": "file_edit", "edits": edits}, metadata)

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None, *, stream_id: str | None = None, stream_end: bool = False, resuming: bool = False, merge_next: bool = False) -> None:
        await self._send({
            "event": "stream_end" if stream_end else "delta", "text": delta,
            "stream_id": stream_id, "resuming": resuming, "merge_next": merge_next,
        }, metadata)
