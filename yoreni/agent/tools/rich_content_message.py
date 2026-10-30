"""Tool that delivers files, links, images, video, and HTML as one card."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from yoreni.agent.tools.base import ToolResult, tool_parameters
from yoreni.agent.tools.chat_delivery import (
    DeliveryTarget,
    OutboundChatTool,
    preview_links,
)
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.path_utils import resolve_workspace_path
from yoreni.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from yoreni.config.paths import get_workspace_path
from yoreni.security.workspace_access import current_tool_workspace

if TYPE_CHECKING:
    from yoreni.agent.state import AgentStore
    from yoreni.bus.events import OutboundMessage


@tool_parameters(
    tool_parameters_schema(
        media=ArraySchema(
            StringSchema(""),
            description=(
                "Files or links to deliver: images, video, HTML pages, http(s) "
                "links, or any other file. Relative paths resolve inside the workspace."
            ),
        ),
        text=StringSchema(
            "Optional Markdown caption shown with the files. Usually omit it: the "
            "message that explains the files belongs in send_message."
        ),
        reply_to=StringSchema(
            "Conversation message ID this card answers. Use an ID shown under "
            "'Recent messages from the user'. Omit for a fresh card."
        ),
        quote=StringSchema(
            "Exact excerpt of the replied message that this card addresses, "
            "when only part of it matters. Requires reply_to."
        ),
        channel=StringSchema(
            "Target channel; defaults to the conversation that triggered this turn."
        ),
        chat_id=StringSchema(
            "Target chat ID; defaults to the current chat. Omit for the conversation "
            "that triggered this turn."
        ),
        required=["media"],
    )
)
class RichContentMessageTool(OutboundChatTool):
    """Deliver files as a standalone card instead of a chat bubble."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        *,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        workspace: str | Path | None = None,
        restrict_to_workspace: bool = False,
        store: AgentStore | None = None,
    ) -> None:
        super().__init__(
            send_callback=send_callback,
            default_channel=default_channel,
            default_chat_id=default_chat_id,
            default_message_id=default_message_id,
            store=store,
        )
        self._workspace = (
            Path(workspace).expanduser() if workspace is not None else get_workspace_path()
        )
        self._restrict_to_workspace = restrict_to_workspace

    @classmethod
    def create(cls, ctx: ToolContext) -> OutboundChatTool:
        return cls(
            send_callback=ctx.bus.publish_outbound if ctx.bus else None,
            workspace=ctx.workspace,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
            store=ctx.agent_store,
        )

    @property
    def name(self) -> str:
        return "rich_content_message"

    @property
    def description(self) -> str:
        return (
            "Deliver files or links to the user as one card: images and video show a "
            "preview, HTML pages open in a sandboxed viewer, links show the page's "
            "title and description, and anything else becomes a download card. The "
            "card is not a chat bubble, so send the sentence that explains them with "
            "send_message."
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
        media: list[str] | None = None,
        text: str = "",
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        reply_to: str | None = None,
        quote: str | None = None,
        **kwargs: Any,
    ) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        from yoreni.utils.helpers import strip_think

        items = [item for item in (media or []) if isinstance(item, str) and item.strip()]
        if not items:
            return ToolResult.error("Error: provide at least one file in media")
        try:
            items = self._resolve_media(items)
        except (OSError, PermissionError, ValueError) as e:
            return ToolResult.error(f"Error: media path is not allowed: {str(e)}")

        resolved = await self._resolve_target(channel, chat_id, message_id)
        if isinstance(resolved, str):
            return ToolResult.error(resolved)
        target: DeliveryTarget = resolved

        normalized_quote, error = await self._validate_reply(reply_to, cast(str | None, quote))
        if error is not None:
            return ToolResult.error(error)

        if self._suppressed:
            logger.debug("RichContentMessageTool: delivery suppressed during internal check")
            return f"Message acknowledged for {target.channel}:{target.chat_id} (not delivered)"

        event = self._build_event(
            content=strip_think(text or "").strip(),
            media=items,
            reply_to=reply_to,
            quote=normalized_quote,
            rich=True,
            links=await preview_links(items),
        )
        error = await self._deliver(target, event, media=items, buttons=[])
        if error is not None:
            return ToolResult.error(error)
        return (
            f"Sent {len(items)} item(s) to {target.channel}:{target.chat_id} "
            f"(id: {event.message_id})."
        )
