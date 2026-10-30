"""Tool that sends a conversational message to the user."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from yoreni.agent.tools.base import ToolResult, tool_parameters
from yoreni.agent.tools.chat_delivery import DeliveryTarget, OutboundChatTool
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from yoreni.utils.link_preview import fetch_link_preview, is_http_url

if TYPE_CHECKING:
    from yoreni.agent.state import AgentStore
    from yoreni.bus.events import OutboundMessage

# Past this size the chat renders a trimmed bubble, so a single message stops
# being readable at a glance. The tool reports the size instead of truncating:
# splitting a long answer is the agent's call, not the transport's.
LONG_MESSAGE_CHARS = 1200
HUGE_MESSAGE_CHARS = 5000

# One link and nothing else is a link card, not a sentence. Matching is
# deliberately strict: prose that mentions a URL, a URL wearing a sentence's
# trailing punctuation, and multi-line text all stay ordinary messages.
_LINK_ONLY = re.compile(r"https?://[^\s<>\x22\x27\x60]+", re.IGNORECASE)
_LINK_TAIL_PUNCTUATION = (
    ".,;:!?)]}\u3001\u3002\uff0c\uff1a\uff01\uff1f\uff09\u201d\u2019\x22\x27"
)


def bare_link(text: str) -> str | None:
    """Return the URL when the message is exactly one link."""
    candidate = text.strip()
    if not candidate or "\n" in candidate or "\r" in candidate:
        return None
    if not _LINK_ONLY.fullmatch(candidate) or candidate.endswith(tuple(_LINK_TAIL_PUNCTUATION)):
        return None
    return candidate if is_http_url(candidate) else None


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema("Message text, in Markdown."),
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
        buttons=ArraySchema(
            ArraySchema(StringSchema("Button label")),
            description="Optional: inline keyboard buttons as list of rows, each row is list of button labels.",
        ),
        required=[],
    )
)
class SendMessageTool(OutboundChatTool):
    """Send one conversational message to the user."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        *,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        store: AgentStore | None = None,
    ) -> None:
        super().__init__(
            send_callback=send_callback,
            default_channel=default_channel,
            default_chat_id=default_chat_id,
            default_message_id=default_message_id,
            store=store,
        )

    @classmethod
    def create(cls, ctx: ToolContext) -> OutboundChatTool:
        return cls(
            send_callback=ctx.bus.publish_outbound if ctx.bus else None,
            store=ctx.agent_store,
        )

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return (
            "Say something to the user. This is how you speak: your own text output "
            "is private working notes and is never delivered. Send one message per "
            "thought, the way a person texts — short reactions, answers, follow-ups. "
            "Use reply_to when answering a specific earlier message. A message that is "
            "only a link is delivered as a link card with the page's title and "
            "description. Send files with rich_content_message instead."
        )

    async def execute(
        self,
        text: str = "",
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        buttons: Any = None,
        reply_to: str | None = None,
        quote: str | None = None,
        **kwargs: Any,
    ) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        from yoreni.utils.helpers import strip_think

        content = strip_think(text or "").strip()

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

        if not content and not button_rows:
            return ToolResult.error("Error: provide text or buttons")

        resolved = await self._resolve_target(channel, chat_id, message_id)
        if isinstance(resolved, str):
            return ToolResult.error(resolved)
        target: DeliveryTarget = resolved

        normalized_quote, error = await self._validate_reply(reply_to, cast(str | None, quote))
        if error is not None:
            return ToolResult.error(error)

        if self._suppressed:
            logger.debug("SendMessageTool: delivery suppressed during internal check")
            return f"Message acknowledged for {target.channel}:{target.chat_id} (not delivered)"

        link = bare_link(content)
        if link is not None:
            # A bare link reads better as a card: the page's own title and
            # description replace a raw URL the reader would have to open.
            preview = await fetch_link_preview(link)
            event = self._build_event(
                content=content, media=[], reply_to=reply_to, quote=normalized_quote,
                rich=True, links=(preview,),
            )
            error = await self._deliver(target, event, media=[], buttons=button_rows or [])
            if error is not None:
                return ToolResult.error(error)
            return (
                f"Sent {link} as a link card to {target.channel}:{target.chat_id} "
                f"(id: {event.message_id}). The card shows the page title and "
                "description fetched server-side; the URL is not repeated as text."
            )

        event = self._build_event(
            content=content, media=[], reply_to=reply_to, quote=normalized_quote, rich=False,
        )
        error = await self._deliver(
            target, event, media=[], buttons=button_rows or [],
        )
        if error is not None:
            return ToolResult.error(error)
        return self._confirmation(
            content=content,
            channel=target.channel,
            chat_id=target.chat_id,
            sent_id=event.message_id,
            buttons=sum(len(row) for row in button_rows or ()),
        )

    @staticmethod
    def _confirmation(
        *, content: str, channel: str, chat_id: str, sent_id: str, buttons: int,
    ) -> str:
        size = len(content)
        parts = [f"Message sent to {channel}:{chat_id} (id: {sent_id})."]
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
