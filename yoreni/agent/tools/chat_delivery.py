"""Shared plumbing for the tools whose effect is one message in one chat."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

from yoreni.agent.injection import current_user_message_id
from yoreni.agent.tools.base import Tool
from yoreni.agent.tools.context import current_memory_scope, current_request_context
from yoreni.bus.delivery import DeliveryRequest
from yoreni.bus.events import OutboundMessage
from yoreni.bus.outbound_events import AgentMessageEvent
from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.utils.link_preview import LinkPreview, fetch_link_preview, is_http_url

if TYPE_CHECKING:
    from yoreni.agent.state import AgentStore
    from yoreni.agent.tools.context import RequestContext

_QUOTE_LIMIT = 280


@dataclass
class SentMessages:
    """Messages this Run sent, for choosing the turn's delivery fallback."""

    count: int = 0
    targets: set[tuple[str, str]] = field(default_factory=set)


_CURRENT_MESSAGE_SENDS: ContextVar[SentMessages | None] = ContextVar(
    "message_sends",
    default=None,
)


@contextmanager
def capture_message_deliveries() -> Generator[SentMessages, None, None]:
    """Record successful message deliveries within one agent run."""
    sends = SentMessages()
    token = _CURRENT_MESSAGE_SENDS.set(sends)
    try:
        yield sends
    finally:
        _CURRENT_MESSAGE_SENDS.reset(token)


def _delivery_gateway() -> Any | None:
    """Return the gateway that owns the VM workspace, if one is running."""
    from yoreni.sandbox.gateway import get_sandbox_gateway

    try:
        return get_sandbox_gateway()
    except SandboxUnavailableError:
        return None


async def _localize_media_path(item: str) -> str:
    """Return a host-readable path, exporting the file when it lives elsewhere."""
    if is_http_url(item):
        return item
    if Path(item).expanduser().is_file():
        return item
    gateway = _delivery_gateway()
    if gateway is None:
        return item
    exported = await gateway.materialize_media(item)
    if exported is None:
        logger.debug("Could not export {} for delivery", item)
        return item
    return exported


async def localize_media_paths(items: list[str]) -> list[str]:
    """Make every local attachment readable by the host that delivers it.

    Agent-created files live in the VM workspace, which the host cannot open.
    Delivery stays host-side, so such a file is exported into the media
    directory first; paths that already resolve on the host pass through.
    """
    return [await _localize_media_path(item) for item in items]


async def preview_links(items: list[str]) -> tuple[LinkPreview, ...]:
    """Preview every external link in a delivery, once per URL.

    The requests are independent and bounded by their own timeouts, so a slow
    site delays only its own card.
    """
    urls = list(dict.fromkeys(item for item in items if is_http_url(item)))
    if not urls:
        return ()
    return tuple(await asyncio.gather(*(fetch_link_preview(url) for url in urls)))


def normalize_quote(quote: str | None) -> str | None:
    if quote is None:
        return None
    text = quote.strip()
    return text[:_QUOTE_LIMIT] if text else None


@dataclass(frozen=True)
class DeliveryTarget:
    """Where one outgoing message goes, and what it answers."""

    channel: str
    chat_id: str
    message_id: str | None
    metadata: dict[str, Any]


class OutboundChatTool(Tool):
    """A tool that delivers one message to one chat.

    Subclasses own their parameters and wording; this class owns target
    resolution, reply validation, delivery and persistence.
    """

    def __init__(
        self,
        *,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        store: AgentStore | None = None,
    ) -> None:
        self._send_callback = send_callback
        self._fallback_channel = default_channel
        self._fallback_chat_id = default_chat_id
        self._fallback_message_id = default_message_id
        self._fallback_metadata: dict[str, Any] = {}
        self._store = store
        self._suppress_delivery_var: ContextVar[bool] = ContextVar(
            "message_suppress_delivery",
            default=False,
        )

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Point this tool at the channel delivery path."""
        self._send_callback = callback

    def set_suppress_delivery(self, active: bool) -> Token[bool]:
        """Acknowledge but don't deliver tool sends (heartbeat internal check)."""
        return self._suppress_delivery_var.set(active)

    def reset_suppress_delivery(self, token: Token[bool]) -> None:
        """Restore previous delivery-suppression state."""
        self._suppress_delivery_var.reset(token)

    @property
    def _suppressed(self) -> bool:
        return self._suppress_delivery_var.get()

    async def _resolve_target(
        self, channel: str | None, chat_id: str | None, message_id: str | None,
    ) -> DeliveryTarget | str:
        """Resolve the conversation to address, or return an error message."""
        request_ctx = current_request_context()
        default_channel = (
            request_ctx.channel if request_ctx is not None else self._fallback_channel
        )
        default_chat_id = (
            request_ctx.chat_id if request_ctx is not None else self._fallback_chat_id
        )
        default_message_id = current_user_message_id() or (
            request_ctx.message_id
            if request_ctx is not None
            else self._fallback_message_id
        )
        default_metadata = (
            request_ctx.metadata
            if request_ctx is not None
            else self._fallback_metadata
        )
        channel = channel or default_channel
        explicit_chat_id = chat_id
        if (
            default_channel == "websocket"
            and channel == "websocket"
            and explicit_chat_id is not None
            and str(explicit_chat_id).strip() != ""
            and str(explicit_chat_id).strip() != str(default_chat_id).strip()
        ):
            return (
                "Error: chat_id does not match the active WebSocket conversation. "
                "Omit chat_id (and usually channel) so delivery uses the current "
                "conversation id from context — WebSocket client_id strings "
                "(e.g. anon-…) are not chat ids."
            )
        if chat_id is None and channel != default_channel:
            # A send to another channel addresses the chat the owner used there
            # last; the current chat id belongs to the current channel only.
            chat_id = await self._last_chat(channel)
            if not chat_id:
                return f"Error: no chat known for channel {channel!r}. Pass chat_id explicitly."
        chat_id = chat_id or default_chat_id

        # Only inherit default message_id when targeting the same channel+chat.
        # Cross-chat sends must not carry the original message_id, because
        # some channels (e.g. Feishu) use it to determine the target
        # conversation via their Reply API, which would route the message
        # to the wrong chat entirely.
        same_target = channel == default_channel and chat_id == default_chat_id
        if same_target:
            message_id = message_id or default_message_id
        else:
            message_id = None

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        metadata = dict(default_metadata) if same_target else {}
        if message_id:
            metadata["message_id"] = message_id
        return DeliveryTarget(channel, chat_id, message_id, metadata)

    async def _validate_reply(
        self, reply_to: str | None, quote: str,
    ) -> tuple[str | None, str | None]:
        """Return the normalized quote plus an error message when it is invalid."""
        normalized = normalize_quote(quote)
        if quote is not None and reply_to is None:
            return None, "Error: quote requires reply_to"
        # Ephemeral Runs have no conversation store to validate against.
        if reply_to is None or self._store is None:
            return normalized, None
        reference = await self._reference(reply_to)
        if reference is None:
            return None, (
                f"Error: unknown reply_to message ID {reply_to!r}. Use an ID from "
                "the recent-messages list, or omit reply_to."
            )
        if normalized is not None and normalized not in reference:
            return None, "Error: quote must be an exact excerpt of the replied message"
        return normalized, None

    async def _deliver(
        self,
        target: DeliveryTarget,
        event: AgentMessageEvent,
        *,
        media: list[str],
        buttons: list[list[str]],
    ) -> str | None:
        """Send one event to its chat; return an error message on failure."""
        if self._send_callback is None:
            return "Error: Message sending not configured"
        request_ctx = current_request_context()
        metadata = dict(target.metadata)
        if media:
            metadata["_record_channel_delivery"] = True
        msg = OutboundMessage(
            channel=target.channel,
            chat_id=target.chat_id,
            content=event.content,
            media=media,
            buttons=buttons,
            metadata=metadata,
            event=event,
            delivery=DeliveryRequest.from_context(
                {**target.metadata, "run_id": request_ctx.turn_id},
            ) if request_ctx is not None else None,
        )
        await self._persist(request_ctx, event)
        try:
            await self._send_callback(msg)
        except Exception as e:
            return f"Error sending message: {str(e)}"
        sends = _CURRENT_MESSAGE_SENDS.get()
        if sends is not None:
            sends.count += 1
            sends.targets.add((target.channel, target.chat_id))
        return None

    def _build_event(
        self, *, content: str, media: list[str], reply_to: str | None,
        quote: str | None, rich: bool, links: tuple[LinkPreview, ...] = (),
    ) -> AgentMessageEvent:
        return AgentMessageEvent(
            message_id=uuid4().hex,
            content=content,
            created_at_ms=int(time.time() * 1000),
            media=tuple(media),
            reply_to=reply_to,
            quote=quote,
            rich=rich,
            links=links,
        )

    async def _last_chat(self, channel: str) -> str | None:
        if self._store is None:
            return None
        try:
            return await asyncio.to_thread(self._store.last_user_chat, channel)
        except Exception:
            logger.opt(exception=True).warning("failed to resolve last chat for {}", channel)
            return None

    async def _reference(self, message_id: str) -> str | None:
        """Return the referenced row's text, or None when the ID is unknown."""
        if self._store is None:
            return None
        try:
            reference = await asyncio.to_thread(
                self._store.resolve_message_reference, message_id,
                scope=current_memory_scope(),
            )
        except Exception:
            logger.opt(exception=True).warning("failed to resolve reply target {}", message_id)
            return None
        return None if reference is None else reference["content"]

    async def _persist(
        self, request_ctx: RequestContext | None, event: AgentMessageEvent,
    ) -> None:
        if self._store is None or request_ctx is None or not request_ctx.turn_id:
            return
        payload: dict[str, Any] = {
            "id": event.message_id,
            "content": event.content,
            "media": list(event.media),
            "reply_to": event.reply_to,
            "quote": event.quote,
            "created_at_ms": event.created_at_ms,
        }
        if event.rich:
            payload["rich"] = True
        if event.links:
            payload["links"] = [
                {
                    "url": link.url,
                    "title": link.title,
                    "description": link.description,
                    "site": link.site,
                }
                for link in event.links
            ]
        try:
            await asyncio.to_thread(
                self._store.append_entry, request_ctx.turn_id, "message", payload,
            )
        except Exception:
            logger.opt(exception=True).warning("failed to persist outbound message")
