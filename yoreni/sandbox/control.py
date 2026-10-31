"""Server-side ownership of the VM desktop and browser.

The state machine is the single authority for who may send input. A client
cannot grant itself control by changing frontend state: every browser and
desktop action asks this object whether the agent still owns the machine, and
the VNC server is also switched to view-only while the agent owns it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from yoreni.sandbox.errors import SandboxControlError
from yoreni.sandbox.models import ControlOwner, ControlSnapshot


class ControlStateMachine:
    def __init__(self) -> None:
        self._owner = ControlOwner.AGENT
        self._epoch = 0
        self._controller_id: str | None = None
        self._since_ms = int(time.time() * 1000)
        self._reason: str | None = None
        self._active_tools = 0
        self._agent_free = asyncio.Event()
        self._agent_free.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[ControlSnapshot], None]] = []

    # -- observation ---------------------------------------------------------

    def snapshot(self) -> ControlSnapshot:
        return ControlSnapshot(
            owner=self._owner,
            epoch=self._epoch,
            controller_id=self._controller_id,
            since_ms=self._since_ms,
            reason=self._reason,
        )

    def add_listener(self, listener: Callable[[ControlSnapshot], None]) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for listener in tuple(self._listeners):
            try:
                listener(snapshot)
            except Exception:
                continue

    def _transition(self, owner: ControlOwner, *, controller: str | None, reason: str | None) -> None:
        self._owner = owner
        self._controller_id = controller
        self._reason = reason
        self._since_ms = int(time.time() * 1000)
        self._epoch += 1
        if owner is ControlOwner.AGENT:
            self._agent_free.set()
        else:
            self._agent_free.clear()
        self._notify()

    # -- agent gate ----------------------------------------------------------

    async def wait_for_agent(self) -> None:
        """Block a tool until the agent owns the machine again."""
        await self._agent_free.wait()

    def ensure_agent(self, action: str) -> None:
        """Reject a desktop or browser action unless the agent owns the input."""
        if self._owner is not ControlOwner.AGENT:
            raise SandboxControlError(
                f"{action} is unavailable while the user owns the desktop"
            )

    @asynccontextmanager
    async def tool_scope(self, name: str) -> AsyncGenerator[None, None]:
        """Track one tool call so takeover can wait for a stable boundary."""
        del name
        await self._agent_free.wait()
        self._active_tools += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._active_tools -= 1
            if self._active_tools <= 0:
                self._active_tools = 0
                self._idle.set()

    @property
    def active_tools(self) -> int:
        return self._active_tools

    # -- transitions ---------------------------------------------------------

    async def take(self, controller_id: str, *, grace_s: float) -> ControlSnapshot:
        """Pause agent tools and hand desktop input to one viewer."""
        if not controller_id:
            raise SandboxControlError("desktop controller identity is required")
        async with self._lock:
            if self._owner is ControlOwner.USER:
                if self._controller_id == controller_id:
                    return self.snapshot()
                raise SandboxControlError("another viewer already owns the desktop")
            if self._owner is ControlOwner.PAUSING:
                raise SandboxControlError("the desktop is already being handed over")
            self._transition(ControlOwner.PAUSING, controller=controller_id, reason="takeover")
            if self._active_tools:
                try:
                    await asyncio.wait_for(self._idle.wait(), timeout=grace_s)
                except TimeoutError:
                    # A long shell command may still be running. It cannot
                    # receive desktop authority because every browser and
                    # desktop entry point re-checks the owner below.
                    pass
            self._transition(ControlOwner.USER, controller=controller_id, reason=None)
            return self.snapshot()

    async def release(self, controller_id: str, *, reason: str | None = None) -> ControlSnapshot:
        """Return input to the agent; only the current controller may do this."""
        async with self._lock:
            if self._owner is not ControlOwner.USER:
                return self.snapshot()
            if self._controller_id != controller_id:
                raise SandboxControlError("only the controlling viewer may release the desktop")
            self._transition(ControlOwner.AGENT, controller=None, reason=reason)
            return self.snapshot()

    async def force_agent(self, *, reason: str) -> ControlSnapshot:
        """Recover from a disconnect, a cancelled Run or a dead VM."""
        async with self._lock:
            self._transition(ControlOwner.AGENT, controller=None, reason=reason)
            return self.snapshot()
