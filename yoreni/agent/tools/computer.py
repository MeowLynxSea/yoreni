"""Graphical desktop tool backed by the VM's X display."""

from __future__ import annotations

from typing import Any

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.sandbox.gateway import get_sandbox_gateway
from yoreni.utils.helpers import build_image_content_blocks

_ACTIONS = (
    "screenshot", "move", "click", "drag", "scroll", "type", "key",
    "clipboard_get", "clipboard_set",
)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema("Desktop operation", enum=list(_ACTIONS)),
        x=IntegerSchema(description="X coordinate", minimum=0, nullable=True),
        y=IntegerSchema(description="Y coordinate", minimum=0, nullable=True),
        end_x=IntegerSchema(description="Drag end X coordinate", minimum=0, nullable=True),
        end_y=IntegerSchema(description="Drag end Y coordinate", minimum=0, nullable=True),
        text=StringSchema("Unicode text to type or put on the clipboard", nullable=True),
        keys=StringSchema("Key or combination, e.g. 'Return' or 'ctrl+c'", nullable=True),
        button=StringSchema("Mouse button: left, right, middle, scroll_up, scroll_down", nullable=True),
        amount=IntegerSchema(description="Scroll or click repeat count", minimum=1, maximum=20, nullable=True),
        required=["action"],
    )
)
class ComputerTool(Tool):
    """Operate the VM desktop with a mouse, keyboard, and clipboard."""

    _scopes = {"core", "subagent"}

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.sandbox.enabled and ctx.config.sandbox.desktop_enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls()

    @property
    def name(self) -> str:
        return "computer"

    @property
    def description(self) -> str:
        return (
            "Control the Yoreni VM desktop directly: screenshot, mouse, keyboard, "
            "drag, scroll, and clipboard. Use the browser tool for structured web "
            "tasks and this tool when a GUI application needs real input."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        action: str,
        x: int | None = None,
        y: int | None = None,
        end_x: int | None = None,
        end_y: int | None = None,
        text: str | None = None,
        keys: str | None = None,
        button: str | None = None,
        amount: int | None = None,
        **kwargs: Any,
    ) -> Any:
        gateway = get_sandbox_gateway()
        try:
            if action == "screenshot":
                raw = await gateway.desktop_screenshot()
                return build_image_content_blocks(
                    raw, "image/png", "desktop://screenshot", "(VM desktop screenshot)",
                )
            if action in {"move", "click", "drag", "scroll"}:
                mapped = {
                    "left": 1, "middle": 2, "right": 3,
                    "scroll_up": 4, "scroll_down": 5,
                }.get(button or "left", 1)
                await gateway.desktop_mouse(
                    action,
                    x=x,
                    y=y,
                    end_x=end_x,
                    end_y=end_y,
                    button=mapped,
                    amount=amount or 3,
                )
                return f"Desktop {action} completed"
            if action == "type":
                await gateway.desktop_type(text or "")
                return f"Typed {len(text or '')} characters on the VM desktop"
            if action == "key":
                if not keys:
                    return ToolResult.error("Error: keys is required")
                await gateway.desktop_key(keys)
                return f"Pressed {keys}"
            if action == "clipboard_get":
                return await gateway.desktop_clipboard()
            if action == "clipboard_set":
                await gateway.desktop_clipboard(text=text or "")
                return "Clipboard updated"
            return ToolResult.error(f"Error: unknown desktop action {action!r}")
        except Exception as exc:
            return ToolResult.error(f"Error: desktop action failed: {exc}")
