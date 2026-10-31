"""Tool that attaches an emoji reaction to a conversation message."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from loguru import logger

from yoreni.agent.injection import current_user_message_id
from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import (
    RequestContext,
    ToolContext,
    current_memory_scope,
    current_request_context,
)
from yoreni.agent.tools.schema import StringSchema, tool_parameters_schema
from yoreni.bus.delivery import DeliveryRequest
from yoreni.bus.events import OutboundMessage
from yoreni.bus.outbound_events import ReactionEvent

if TYPE_CHECKING:
    from yoreni.agent.state import AgentStore

_EMOJI_MAX_POINTS = 16
# The reaction round must not force a fake feeling: the agent may decline.
NONE_EMOJI = "none"


def _clean_emoji(value: str) -> str | None:
    emoji = value.strip()
    if not emoji or len(emoji) > _EMOJI_MAX_POINTS:
        return None
    if any(character.isspace() for character in emoji):
        return None
    if any(character.isascii() and character.isalnum() for character in emoji):
        return None
    return emoji


@tool_parameters(
    tool_parameters_schema(
        emoji=StringSchema(
            "One emoji that expresses your first reaction, e.g. 😊, 👀, 👍, 🤔. No words. "
            "Pass NONE when no emoji fits the message."
        ),
        message_id=StringSchema(
            "Conversation message ID to react to. Defaults to the newest message "
            "from the user. Pass an ID only to react to an older message."
        ),
        required=["emoji"],
    )
)
class ReactTool(Tool):
    """React to one conversation message without sending a reply."""

    def __init__(
        self,
        *,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        store: AgentStore | None = None,
    ) -> None:
        self._send_callback = send_callback
        self._store = store

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(
            send_callback=ctx.bus.publish_outbound if ctx.bus else None,
            store=ctx.agent_store,
        )

    @property
    def name(self) -> str:
        return "react"

    @property
    def description(self) -> str:
        return (
            "Put one emoji reaction on a message. Do it first, before you answer, "
            "to acknowledge what the user just said. Pass NONE when no emoji fits."
        )

    async def execute(self, emoji: str = "", message_id: str | None = None, **kwargs: object) -> str:
        if emoji.strip().lower() == NONE_EMOJI:
            return "No reaction sent."
        cleaned = _clean_emoji(emoji)
        if cleaned is None:
            return ToolResult.error("Error: emoji must be a single emoji without words")

        request_ctx = current_request_context()
        newest = current_user_message_id()
        target = message_id or newest or (
            request_ctx.message_id if request_ctx is not None else None
        )
        if not target:
            return ToolResult.error(
                "Error: no message to react to. Pass message_id explicitly."
            )

        if self._store is not None:
            reference = await self._reference(target)
            if reference is None:
                return ToolResult.error(
                    f"Error: unknown message ID {target!r}. Use an ID from the "
                    "recent-messages list, or omit message_id."
                )

        if not self._send_callback:
            return ToolResult.error("Error: Message sending not configured")

        created_at_ms = int(time.time() * 1000)
        event = ReactionEvent(
            message_id=uuid4().hex,
            emoji=cleaned,
            created_at_ms=created_at_ms,
            target=target,
        )
        await self._persist(request_ctx, event)

        channel = request_ctx.channel if request_ctx is not None else "cli"
        chat_id = request_ctx.chat_id if request_ctx is not None else "direct"
        metadata = dict(request_ctx.metadata) if request_ctx is not None else {}
        await self._send_callback(OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content="",
            metadata=metadata,
            event=event,
            delivery=(DeliveryRequest.from_context(
                {**metadata, "run_id": request_ctx.turn_id},
            ) if request_ctx is not None else None),
        ))
        return f"Reacted {cleaned} to message {target}."

    async def _reference(self, message_id: str) -> str | None:
        assert self._store is not None
        try:
            reference = await asyncio.to_thread(
                self._store.resolve_message_reference, message_id,
                scope=current_memory_scope(),
            )
        except Exception:
            logger.opt(exception=True).warning("failed to resolve reaction target {}", message_id)
            return None
        return None if reference is None else reference["content"]

    async def _persist(self, request_ctx: RequestContext | None, event: ReactionEvent) -> None:
        if self._store is None or request_ctx is None or not request_ctx.turn_id:
            return
        turn_id = request_ctx.turn_id
        payload = {
            "id": event.message_id,
            "target": event.target,
            "emoji": event.emoji,
            "created_at_ms": event.created_at_ms,
        }
        try:
            await asyncio.to_thread(self._store.append_entry, turn_id, "reaction", payload)
        except Exception:
            logger.opt(exception=True).warning("failed to persist reaction")
