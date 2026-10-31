"""Structured browser tool backed by Chromium in the VM."""

from __future__ import annotations

from typing import Any

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.sandbox.gateway import get_sandbox_gateway
from yoreni.utils.helpers import build_image_content_blocks

_ACTIONS = (
    "navigate", "snapshot", "click", "type", "press", "scroll", "evaluate",
    "screenshot", "tabs", "open_tab", "select_tab", "upload", "downloads",
)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema("Browser operation", enum=list(_ACTIONS)),
        url=StringSchema("URL for navigate or open_tab", nullable=True),
        selector=StringSchema("CSS selector for click, type, or upload", nullable=True),
        text=StringSchema("Text to type", nullable=True),
        keys=StringSchema("Key or key combination, e.g. 'Enter' or 'Control+A'", nullable=True),
        expression=StringSchema("JavaScript expression to evaluate in the page", nullable=True),
        paths=StringSchema("Comma-separated VM paths for upload", nullable=True),
        button=StringSchema("Mouse button: left, right, or middle", nullable=True),
        click_count=IntegerSchema(description="Number of clicks", minimum=1, maximum=3, nullable=True),
        index=IntegerSchema(description="Tab index", minimum=0, nullable=True),
        dx=IntegerSchema(description="Horizontal scroll amount", nullable=True),
        dy=IntegerSchema(description="Vertical scroll amount", nullable=True),
        submit=BooleanSchema(description="Press Enter after typing", default=False),
        full_page=BooleanSchema(description="Capture the full page", default=False),
        required=["action"],
    )
)
class BrowserTool(Tool):
    """Navigate and inspect the VM Chromium instance."""

    _scopes = {"core", "subagent"}

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.sandbox.enabled and ctx.config.sandbox.browser_enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls()

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Drive the Chromium browser inside the Yoreni VM. "
            "Use snapshot before clicking or typing; the agent must observe the "
            "page again after the user returns desktop control."
        )

    @property
    def exclusive(self) -> bool:
        return True

    # Actions that act on the page already open, without naming it themselves.
    _PAGE_ACTIONS = frozenset({"click", "type", "press", "scroll", "evaluate", "upload"})

    def credential_destination(self, params: dict[str, Any]) -> str | None:
        """Report the page a credential would be typed into."""
        if params.get("action") not in self._PAGE_ACTIONS:
            return None
        try:
            return get_sandbox_gateway().browser.current_url
        except Exception:
            return None

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        action: str,
        url: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        keys: str | None = None,
        expression: str | None = None,
        paths: str | None = None,
        button: str | None = None,
        click_count: int | None = None,
        index: int | None = None,
        dx: int | None = None,
        dy: int | None = None,
        submit: bool = False,
        full_page: bool = False,
        **kwargs: Any,
    ) -> Any:
        gateway = get_sandbox_gateway()
        browser = gateway.browser
        try:
            if action == "navigate":
                return await browser.navigate(url or "")
            if action == "snapshot":
                return await browser.snapshot()
            if action == "click":
                return await browser.click(
                    selector or "",
                    button=button or "left",
                    click_count=click_count or 1,
                )
            if action == "type":
                return await browser.type_text(
                    selector or "", text or "", submit=submit,
                )
            if action == "press":
                return await browser.press(keys or "")
            if action == "scroll":
                return await browser.scroll(dx=dx or 0, dy=dy or 600)
            if action == "evaluate":
                return await browser.evaluate(expression or "")
            if action == "screenshot":
                raw = await browser.screenshot(full_page=full_page)
                return build_image_content_blocks(
                    raw, "image/png", "browser://screenshot", "(VM browser screenshot)",
                )
            if action == "tabs":
                return await browser.list_tabs()
            if action == "open_tab":
                return await browser.open_tab(url or "about:blank")
            if action == "select_tab":
                return await browser.select_tab(index or 0)
            if action == "upload":
                selected = [part.strip() for part in (paths or "").split(",") if part.strip()]
                return await browser.upload(selector or "", selected)
            if action == "downloads":
                entries = await gateway.list_dir(gateway.config.downloads_dir)
                if not entries:
                    return "No downloaded files in the VM."
                return "\n".join(
                    f"{entry.path} ({entry.size} bytes)"
                    for entry in entries
                    if entry.is_file
                )
            return ToolResult.error(f"Error: unknown browser action {action!r}")
        except Exception as exc:
            return ToolResult.error(f"Error: browser action failed: {exc}")
