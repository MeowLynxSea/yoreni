# pyright: reportMissingImports=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

"""Structured browser automation against Chromium inside the VM.

Playwright connects to the VM's CDP endpoint. The browser process, profile,
downloads and uploads all live inside the VM; the host only speaks CDP to it.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from loguru import logger

from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.control import ControlStateMachine
from yoreni.sandbox.errors import SandboxControlError, SandboxUnavailableError
from yoreni.sandbox.paths import PathRoots, normalize_vm_path

_MAX_TEXT = 40_000
_BLOCKED_SCHEMES = ("file:", "chrome:", "devtools:", "view-source:", "javascript:", "data:")


class BrowserService:
    """One persistent Chromium session owned by the gateway."""

    def __init__(
        self,
        config: SandboxConfig,
        control: ControlStateMachine,
        *,
        roots: PathRoots,
        url_guard: Any | None = None,
    ) -> None:
        self._config = config
        self._control = control
        self._roots = roots
        self._url_guard = url_guard
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self._observed_epoch = -1

    @property
    def connected(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def connect(self, cdp_url: str) -> None:
        if self.connected:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise SandboxUnavailableError(
                "Playwright is required for browser tools. Install the 'sandbox' extra."
            ) from exc
        try:
            self._playwright = await async_playwright().start()
            ws_url = await asyncio.to_thread(_debugger_ws_url, cdp_url)
            self._browser = await self._playwright.chromium.connect_over_cdp(ws_url)
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else await self._browser.new_context()
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            await self._configure_downloads()
        except Exception as exc:
            await self.close()
            raise SandboxUnavailableError(f"could not attach to the VM browser: {exc}") from exc

    async def _configure_downloads(self) -> None:
        if self._page is None:
            return
        try:
            session = await self._context.new_cdp_session(self._page)
            await session.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": self._config.downloads_dir,
                    "eventsEnabled": True,
                },
            )
        except Exception:
            logger.warning("Could not configure VM download directory")

    async def close(self) -> None:
        if self._browser is not None:
            try:
                # Disconnect only; the VM browser and its profile stay alive.
                await self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    # -- control -------------------------------------------------------------

    def invalidate_observation(self) -> None:
        self._observed_epoch = -1

    def _ensure_action(self, action: str) -> None:
        self._control.ensure_agent(action)
        if self._observed_epoch != self._control.snapshot().epoch:
            raise SandboxControlError(
                f"{action} requires a fresh browser snapshot after control changed"
            )

    async def _page_or_fail(self) -> Any:
        if not self.connected or self._page is None:
            raise SandboxUnavailableError("the VM browser is not connected")
        return self._page

    async def _check_url(self, url: str) -> str:
        value = url.strip()
        if not value:
            raise ValueError("url must not be empty")
        lowered = value.lower()
        if any(lowered.startswith(scheme) for scheme in _BLOCKED_SCHEMES):
            raise ValueError("that URL scheme is not available to the agent")
        if self._url_guard is not None:
            await asyncio.to_thread(self._url_guard, value)
        return value

    # -- observation ---------------------------------------------------------

    async def snapshot(self) -> str:
        async with self._lock:
            self._control.ensure_agent("browser snapshot")
            page = await self._page_or_fail()
            title = await page.title()
            url = page.url
            try:
                tree = await page.locator("body").aria_snapshot(timeout=10_000)
            except Exception:
                tree = await page.locator("body").inner_text(timeout=10_000)
            text = str(tree or "")
            if len(text) > _MAX_TEXT:
                text = text[:_MAX_TEXT] + "\n... (snapshot truncated)"
            self._observed_epoch = self._control.snapshot().epoch
            return (
                f"URL: {url}\nTitle: {title}\nControl epoch: {self._observed_epoch}\n\n{text}"
            )

    async def navigate(self, url: str) -> str:
        target = await self._check_url(url)
        async with self._lock:
            self._control.ensure_agent("browser navigation")
            page = await self._page_or_fail()
            response = await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            self._observed_epoch = self._control.snapshot().epoch
            status = response.status if response is not None else "unknown"
            return f"Navigated to {page.url} (status {status})\nTitle: {await page.title()}"

    async def click(self, selector: str, *, button: str = "left", click_count: int = 1) -> str:
        async with self._lock:
            self._ensure_action("browser click")
            page = await self._page_or_fail()
            await page.locator(selector).click(
                button=button, click_count=click_count, timeout=15_000
            )
            return f"Clicked {selector}"

    async def type_text(self, selector: str, text: str, *, submit: bool = False) -> str:
        async with self._lock:
            self._ensure_action("browser typing")
            page = await self._page_or_fail()
            locator = page.locator(selector)
            await locator.fill(text, timeout=15_000)
            if submit:
                await locator.press("Enter")
            return f"Entered {len(text)} characters into {selector}"

    async def press(self, keys: str) -> str:
        async with self._lock:
            self._ensure_action("browser keyboard")
            page = await self._page_or_fail()
            await page.keyboard.press(keys)
            return f"Pressed {keys}"

    async def scroll(self, *, dx: int = 0, dy: int = 600) -> str:
        async with self._lock:
            self._ensure_action("browser scroll")
            page = await self._page_or_fail()
            await page.mouse.wheel(dx, dy)
            return f"Scrolled by ({dx}, {dy})"

    async def evaluate(self, expression: str) -> str:
        async with self._lock:
            self._ensure_action("browser evaluate")
            page = await self._page_or_fail()
            result = await page.evaluate(expression)
            text = str(result)
            return text[:_MAX_TEXT]

    async def screenshot(self, *, full_page: bool = False) -> bytes:
        async with self._lock:
            self._control.ensure_agent("browser screenshot")
            page = await self._page_or_fail()
            data = await page.screenshot(full_page=full_page, type="png")
            self._observed_epoch = self._control.snapshot().epoch
            return bytes(data)

    async def list_tabs(self) -> str:
        async with self._lock:
            self._control.ensure_agent("browser tabs")
            if self._context is None:
                raise SandboxUnavailableError("the VM browser is not connected")
            lines: list[str] = []
            for index, page in enumerate(self._context.pages):
                lines.append(f"{index}: {page.url} | {await page.title()}")
            return "\n".join(lines)

    async def open_tab(self, url: str = "about:blank") -> str:
        target = await self._check_url(url) if url != "about:blank" else url
        async with self._lock:
            self._control.ensure_agent("browser tab")
            if self._context is None:
                raise SandboxUnavailableError("the VM browser is not connected")
            page = await self._context.new_page()
            if target != "about:blank":
                await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            self._page = page
            self._observed_epoch = self._control.snapshot().epoch
            return f"Opened tab {len(self._context.pages) - 1}: {page.url}"

    async def select_tab(self, index: int) -> str:
        async with self._lock:
            self._control.ensure_agent("browser tab")
            if self._context is None:
                raise SandboxUnavailableError("the VM browser is not connected")
            pages = self._context.pages
            if index < 0 or index >= len(pages):
                raise ValueError("tab index is out of range")
            self._page = pages[index]
            await self._page.bring_to_front()
            self._observed_epoch = self._control.snapshot().epoch
            return f"Selected tab {index}: {self._page.url}"

    async def upload(self, selector: str, paths: Sequence[str]) -> str:
        resolved: list[str] = []
        for raw in paths:
            path = normalize_vm_path(raw, cwd=self._config.workspace)
            self._roots.check_read(path)
            resolved.append(path)
        async with self._lock:
            self._ensure_action("browser upload")
            page = await self._page_or_fail()
            await page.locator(selector).set_input_files(resolved, timeout=15_000)
            return f"Selected {len(resolved)} file(s) for {selector}"


def _debugger_ws_url(cdp_url: str) -> str:
    """Rewrite the guest-local CDP websocket URL to the forwarded host port."""
    import json
    from urllib.parse import urlsplit, urlunsplit
    from urllib.request import urlopen

    with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=10) as response:
        payload = json.load(response)
    raw = str(payload.get("webSocketDebuggerUrl") or "")
    if not raw:
        return cdp_url
    guest = urlsplit(raw)
    host = urlsplit(cdp_url)
    return urlunsplit((guest.scheme, host.netloc, guest.path, guest.query, guest.fragment))
