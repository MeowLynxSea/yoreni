"""First-run preparation of the sandbox VM, driven by onboarding.

Starting the sandbox pulls the image, provisions the VM and starts its services.
That takes minutes, so onboarding runs it as a background task and polls the
gateway's progress instead of blocking a request.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.gateway import SandboxGateway, get_sandbox_gateway
from yoreni.sandbox.podroid_backend import HttpVmControl, host_control

_setup: "SandboxSetup | None" = None


def get_sandbox_setup() -> "SandboxSetup":
    """Return the process-wide sandbox setup task owner."""
    global _setup
    if _setup is None:
        _setup = SandboxSetup()
    return _setup


class SandboxSetup:
    """Own the one background first-start sequence the WebUI can observe."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        gateway = self._gateway()
        if gateway is None:
            return self._unsupported("The sandbox is not configured for this instance")
        reason = self._unsupported_reason(gateway)
        if reason is not None:
            return self._unsupported(reason)
        state = gateway.progress.snapshot()
        settled = self._task is None or self._task.done()
        if settled and gateway.started and state["state"] in {"idle", "running"}:
            # A previous run (or a restart) already prepared this VM.
            gateway.progress.finish()
            return gateway.progress.snapshot()
        return state

    def is_ready(self) -> bool:
        return self.snapshot()["state"] in {"ready", "unsupported"}

    def host_mode(self) -> str:
        """`podroid` when a separate Android app owns the VM, else `desktop`."""
        gateway = self._gateway()
        if gateway is not None and host_control(gateway.config) is not None:
            return "podroid"
        return "desktop"

    async def host_status(self) -> dict[str, Any] | None:
        """What the host app knows: Podroid installed, VM answering.

        None when this instance has no host app to ask, which is every desktop.
        """
        gateway = self._gateway()
        control = None if gateway is None else host_control(gateway.config)
        if control is None:
            return None
        return {"mode": "podroid", **await control.status()}

    async def start_host(self) -> None:
        """Ask the host app to boot the VM, without waiting for it."""
        control = self._host_control()
        await control("start")

    async def install_host(self) -> None:
        """Send the owner to the Podroid download their phone can install."""
        control = self._host_control()
        await control.install()

    # -- writes --------------------------------------------------------------

    async def ensure_started(self) -> dict[str, Any]:
        gateway = self._gateway()
        if gateway is None or self._unsupported_reason(gateway) is not None:
            return self.snapshot()
        if gateway.started:
            return self.snapshot()
        async with self._lock:
            if self._task is None or self._task.done():
                if gateway.progress.snapshot()["state"] != "running":
                    gateway.progress.start()
                self._task = asyncio.create_task(self._run(gateway))
        return self.snapshot()

    async def retry(self) -> dict[str, Any]:
        gateway = self._gateway()
        if gateway is None or self._unsupported_reason(gateway) is not None:
            return self.snapshot()
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self.snapshot()
            gateway.progress.start()
            self._task = asyncio.create_task(self._run(gateway))
        return self.snapshot()

    async def rebuild(self) -> dict[str, Any]:
        """Recreate the VM, keeping its workspace.

        This is also the recovery path for a VM that failed to start, for
        example after the default image changed, so it must not require a
        running gateway: the rebuild boots the old box itself to take the
        backup.
        """
        gateway = self._gateway()
        if gateway is None or self._unsupported_reason(gateway) is not None:
            return self.snapshot()
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self.snapshot()
            gateway.progress.start()
            self._task = asyncio.create_task(self._run(gateway, gateway.rebuild))
        return self.snapshot()

    async def _run(
        self,
        gateway: SandboxGateway,
        operation: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        try:
            await (operation or gateway.start)()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through failure states
            logger.warning("Sandbox setup failed: {}", exc)
            gateway.progress.fail(str(exc))
        else:
            gateway.progress.finish()

    # -- helpers -------------------------------------------------------------

    def _gateway(self) -> SandboxGateway | None:
        try:
            return get_sandbox_gateway()
        except SandboxUnavailableError:
            return None

    def _host_control(self) -> HttpVmControl:
        gateway = self._gateway()
        control = None if gateway is None else host_control(gateway.config)
        if control is None:
            raise SandboxUnavailableError(
                "This instance has no host app to start the VM"
            )
        return control

    def _unsupported_reason(self, gateway: SandboxGateway) -> str | None:
        if not gateway.config.enabled:
            return "The sandbox is turned off for this instance"
        if sys.platform not in {"darwin", "linux"}:
            return "The sandbox needs macOS or Linux"
        return None

    def _unsupported(self, detail: str) -> dict[str, Any]:
        return {
            "state": "unsupported",
            "stage": "ready",
            "percent": 100.0,
            "detail": detail,
            "error": "",
            "downloaded_bytes": None,
            "total_bytes": None,
            "rate_bytes_per_s": None,
            "eta_seconds": None,
            "elapsed_s": 0.0,
        }
