"""Tool that sends a conversational message to the user."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from loguru import logger

from yoreni.agent.injection import current_user_message_id
from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext, current_request_context
from yoreni.agent.tools.path_utils import resolve_workspace_path
from yoreni.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from yoreni.bus.delivery import DeliveryRequest
from yoreni.bus.events import OutboundMessage
from yoreni.bus.outbound_events import AgentMessageEvent
from yoreni.config.paths import get_workspace_path
from yoreni.security.workspace_access import current_tool_workspace

if TYPE_CHECKING:
    from yoreni.agent.state import AgentStore
    from yoreni.agent.tools.context import RequestContext

# Past this size the chat renders a trimmed bubble, so a single message stops
# being readable at a glance. The tool reports the size instead of truncating:
# splitting a long answer is the agent's call, not the transport's.
LONG_MESSAGE_CHARS = 1200
HUGE_MESSAGE_CHARS = 5000

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
    """Record successful send_message targets within one agent run."""
    sends = SentMessages()
    token = _CURRENT_MESSAGE_SENDS.set(sends)
    try:
        yield sends
    finally:
        _CURRENT_MESSAGE_SENDS.reset(token)


def _normalize_quote(quote: str | None) -> str | None:
    if quote is None:
        return None
    text = quote.strip()
    return text[:_QUOTE_LIMIT] if text else None


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema(
            "Message text, in Markdown. Leave empty only when sending attachments."
        ),
        reply_to=StringSchema(
            "Conversation message ID this message answers. Use an ID shown under "
            "'Recent messages from the user'. Omit for a fresh message."
        ),
        quote=StringSchema(
            "Exact excerpt of the replied message that this message addresses, "
            "when only part of it matters. Requires reply_to."
        ),
        channel=StringSchema(
            "Target channel; defaults to the conversation that triggered this turn."
        ),
        chat_id=StringSchema(
            "Target chat ID; defaults to the current chat. Omit for the conversation "
            "that triggered this turn."
        ),
        media=ArraySchema(
            StringSchema(""),
            description=(
                "Optional existing files to attach: images, video, HTML pages, or any "
                "other file. Relative paths resolve inside the workspace."
            ),
        ),
        buttons=ArraySchema(
            ArraySchema(StringSchema("Button label")),
            description="Optional: inline keyboard buttons as list of rows, each row is list of button labels.",
        ),
        required=[],
    )
)
class SendMessageTool(Tool):
    """Send one conversational message to the user."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        workspace: str | Path | None = None,
        restrict_to_workspace: bool = False,
        store: AgentStore | None = None,
    ):
        self._send_callback = send_callback
        self._workspace = (
            Path(workspace).expanduser() if workspace is not None else get_workspace_path()
        )
        self._restrict_to_workspace = restrict_to_workspace
        self._fallback_channel = default_channel
        self._fallback_chat_id = default_chat_id
        self._fallback_message_id = default_message_id
        self._fallback_metadata: dict[str, Any] = {}
        self._store = store
        self._suppress_delivery_var: ContextVar[bool] = ContextVar(
            "message_suppress_delivery",
            default=False,
        )

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        send_callback = ctx.bus.publish_outbound if ctx.bus else None
        return cls(
            send_callback=send_callback,
            workspace=ctx.workspace,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
            store=ctx.agent_store,
        )

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def set_suppress_delivery(self, active: bool) -> Token[bool]:
        """Acknowledge but don't deliver tool sends (heartbeat internal check)."""
        return self._suppress_delivery_var.set(active)

    def reset_suppress_delivery(self, token: Token[bool]) -> None:
        """Restore previous delivery-suppression state."""
        self._suppress_delivery_var.reset(token)

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return (
            "Send a message to the user. This is how you speak: your own text output "
            "is private working notes and is never delivered. Send one message per "
            "thought, the way a person texts — short reactions, answers, follow-ups, "
            "and attachments as separate messages. Use reply_to when answering a "
            "specific earlier message. Attach files with media."
        )

    def _resolve_media(self, media: list[str]) -> list[str]:
        """Resolve local media attachments and enforce workspace restriction when enabled."""
        resolved: list[str] = []
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
        )
        workspace = access.project_path or self._workspace
        for p in media:
            if p.startswith(("http://", "https://")):
                resolved.append(p)
            elif not access.restrict_to_workspace:
                path = Path(p).expanduser()
                resolved.append(p if path.is_absolute() else str(workspace / path))
            else:
                resolved.append(str(resolve_workspace_path(p, workspace, access.allowed_root)))
        return resolved

    async def execute(
        self,
        text: str = "",
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        buttons: Any = None,
        reply_to: str | None = None,
        quote: str | None = None,
        **kwargs: Any,
    ) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        from yoreni.utils.helpers import strip_think

        content = strip_think(text or "").strip()
        media = [item for item in (media or []) if isinstance(item, str) and item.strip()]

        button_rows: list[list[str]] | None = None
        if buttons is not None:
            raw_buttons = cast(list[Any], buttons) if isinstance(buttons, list) else None
            if raw_buttons is None or any(
                not isinstance(row, list)
                or any(not isinstance(label, str) for label in cast(list[Any], row))
                for row in raw_buttons
            ):
                return ToolResult.error("Error: buttons must be a list of list of strings")
            button_rows = cast(list[list[str]], raw_buttons)

        if not content and not media and not button_rows:
            return ToolResult.error("Error: provide text, media, or buttons")

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
            return ToolResult.error(
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
                return ToolResult.error(
                    f"Error: no chat known for channel {channel!r}. Pass chat_id explicitly."
                )
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
            return ToolResult.error("Error: No target channel/chat specified")

        if not self._send_callback:
            return ToolResult.error("Error: Message sending not configured")

        if media:
            try:
                media = self._resolve_media(media)
            except (OSError, PermissionError, ValueError) as e:
                return ToolResult.error(f"Error: media path is not allowed: {str(e)}")

        normalized_quote = _normalize_quote(quote)
        if quote is not None and reply_to is None:
            return ToolResult.error("Error: quote requires reply_to")
        # Ephemeral Runs have no conversation store to validate against.
        can_resolve = reply_to is not None and self._store is not None
        reference = await self._reference(cast(str, reply_to)) if can_resolve else None
        if can_resolve and reference is None:
            return ToolResult.error(
                f"Error: unknown reply_to message ID {reply_to!r}. Use an ID from "
                "the recent-messages list, or omit reply_to."
            )
        if normalized_quote is not None and reference is not None and normalized_quote not in reference:
            return ToolResult.error(
                "Error: quote must be an exact excerpt of the replied message"
            )

        metadata = dict(default_metadata) if same_target else {}
        if message_id:
            metadata["message_id"] = message_id
        if media:
            metadata["_record_channel_delivery"] = True

        sent_id = uuid4().hex
        created_at_ms = int(time.time() * 1000)
        event = AgentMessageEvent(
            message_id=sent_id,
            content=content,
            created_at_ms=created_at_ms,
            media=tuple(media or ()),
            reply_to=reply_to,
            quote=normalized_quote,
        )

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            buttons=button_rows or [],
            metadata=metadata,
            event=event,
            delivery=(DeliveryRequest.from_context(
                {**default_metadata, "run_id": request_ctx.turn_id},
            ) if request_ctx is not None else None),
        )

        if self._suppress_delivery_var.get():
            logger.debug("SendMessageTool: delivery suppressed during internal check")
            return f"Message acknowledged for {channel}:{chat_id} (not delivered)"

        await self._persist(request_ctx, event)
        try:
            await self._send_callback(msg)
        except Exception as e:
            return ToolResult.error(f"Error sending message: {str(e)}")

        sends = _CURRENT_MESSAGE_SENDS.get()
        if sends is not None:
            sends.count += 1
            sends.targets.add((channel, chat_id))

        return self._confirmation(
            content=content,
            message_count=len(media or ()),
            channel=channel,
            chat_id=chat_id,
            sent_id=sent_id,
            buttons=sum(len(row) for row in button_rows or ()),
        )

    @staticmethod
    def _confirmation(
        *,
        content: str,
        message_count: int,
        channel: str,
        chat_id: str,
        sent_id: str,
        buttons: int,
    ) -> str:
        size = len(content)
        parts = [f"Message sent to {channel}:{chat_id} (id: {sent_id})."]
        if message_count:
            parts.append(f"{message_count} attachment(s) delivered.")
        if buttons:
            parts.append(f"{buttons} button(s) delivered.")
        if size >= HUGE_MESSAGE_CHARS:
            parts.append(
                f"This message is {size} characters. The chat trims bubbles this long; "
                "send the next answer as several shorter messages instead of one wall of text."
            )
        elif size >= LONG_MESSAGE_CHARS:
            parts.append(
                f"This message is {size} characters, so the chat shows a trimmed bubble. "
                "Prefer two or three shorter messages when the content allows it."
            )
        return " ".join(parts)

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
            reference = await asyncio.to_thread(self._store.resolve_message_reference, message_id)
        except Exception:
            logger.opt(exception=True).warning("failed to resolve reply target {}", message_id)
            return None
        return None if reference is None else reference["content"]

    async def _persist(self, request_ctx: RequestContext | None, event: AgentMessageEvent) -> None:
        if self._store is None or request_ctx is None or not request_ctx.turn_id:
            return
        payload = {
            "id": event.message_id,
            "content": event.content,
            "media": list(event.media),
            "reply_to": event.reply_to,
            "quote": event.quote,
            "created_at_ms": event.created_at_ms,
        }
        try:
            await asyncio.to_thread(
                self._store.append_entry, request_ctx.turn_id, "message", payload,
            )
        except Exception:
            logger.opt(exception=True).warning("failed to persist outbound message")
