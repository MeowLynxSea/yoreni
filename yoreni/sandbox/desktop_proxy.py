"""Authenticated byte relay for the VM desktop.

The WebUI never reaches VNC. It opens one WebSocket to the gateway with a
short-lived, single-use ticket; the gateway connects to websockify inside the
VM and relays RFB frames. Input authority is enforced in the VM by x11vnc's
view-only mode, so a modified client cannot type while the agent owns the
desktop.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from loguru import logger
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedOK

from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.gateway import SandboxGateway


@dataclass
class DesktopTokenStore:
    ttl_s: int
    controller_ttl_s: int = 8 * 60 * 60
    _tokens: dict[str, float] = field(default_factory=dict)
    _controllers: dict[str, float] = field(default_factory=dict)

    def issue(self) -> str:
        self._purge()
        value = f"nbdk_{secrets.token_urlsafe(32)}"
        self._tokens[value] = time.monotonic() + self.ttl_s
        return value

    def consume(self, token: str | None) -> bool:
        if not token:
            return False
        self._purge()
        expiry = self._tokens.pop(token, None)
        if expiry is None or time.monotonic() > expiry:
            return False
        self._controllers[token] = time.monotonic() + self.controller_ttl_s
        return True

    def is_controller(self, controller_id: str) -> bool:
        self._purge()
        if controller_id not in self._controllers:
            return False
        self._controllers[controller_id] = time.monotonic() + self.controller_ttl_s
        return True

    def revoke(self, controller_id: str) -> None:
        self._controllers.pop(controller_id, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for token, expiry in list(self._tokens.items()):
            if expiry < now:
                self._tokens.pop(token, None)
        for token, expiry in list(self._controllers.items()):
            if expiry < now:
                self._controllers.pop(token, None)


class DesktopProxy:
    """Relay one authenticated WebSocket to websockify in the VM."""

    def __init__(self, gateway: SandboxGateway, tokens: DesktopTokenStore) -> None:
        self._gateway = gateway
        self._tokens = tokens

    def issue_ticket(self) -> str:
        return self._tokens.issue()

    def authorize(self, ticket: str | None) -> str | None:
        if not ticket or not self._tokens.consume(ticket):
            return None
        return ticket

    def is_controller(self, controller_id: str) -> bool:
        return self._tokens.is_controller(controller_id)

    async def serve(self, connection: object, controller_id: str) -> None:
        try:
            upstream_url = await self._gateway.desktop_websocket_url()
        except SandboxUnavailableError as exc:
            await _close(connection, 1011, str(exc))
            return
        await self._gateway.viewer_connected(controller_id)
        try:
            async with ws_connect(
                upstream_url,
                # websockify defaults to base64 text frames unless the
                # handshake asks for binary; RFB is raw bytes.
                subprotocols=["binary"],
                max_size=None,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=10,
            ) as upstream:
                try:
                    ended_by = await _relay(connection, upstream)
                except _RelayEndedError as exc:
                    if isinstance(exc.cause, ConnectionClosedOK):
                        logger.debug("VM desktop stream closed: {}", exc)
                    else:
                        logger.warning(
                            "VM desktop stream ended: {}{}", exc, await self._log_tail(),
                        )
                else:
                    logger.debug("VM desktop stream closed by the {}", ended_by)
        except Exception as exc:
            logger.warning(
                "VM desktop stream ended: {}{}", exc, await self._log_tail(),
            )
        finally:
            self._tokens.revoke(controller_id)
            await self._gateway.viewer_disconnected(controller_id)

    async def _log_tail(self) -> str:
        """Best-effort service diagnostics; the VM may already be gone."""
        try:
            return "\n" + await asyncio.wait_for(
                self._gateway.desktop.diagnostics(), timeout=5,
            )
        except Exception:
            return ""


class _RelayEndedError(RuntimeError):
    """Which side of the desktop relay closed the session, and how."""

    def __init__(self, side: str, cause: BaseException) -> None:
        super().__init__(f"{side} side closed: {cause!r}")
        self.cause = cause


async def _relay(client: object, upstream: object) -> str:
    async def pump(source: object, sink: object, side: str) -> str:
        try:
            async for message in source:  # type: ignore[attr-defined]
                await sink.send(message)  # type: ignore[attr-defined]
        except Exception as exc:
            raise _RelayEndedError(side, exc) from exc
        return side

    tasks = {
        asyncio.create_task(pump(client, upstream, "client")): "client",
        asyncio.create_task(pump(upstream, client, "upstream")): "upstream",
    }
    try:
        done, pending = await asyncio.wait(
            set(tasks), return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            with_exc = task.exception()
            if with_exc is not None:
                raise with_exc
            return tasks[task]
        return ""
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def _close(connection: object, code: int, reason: str) -> None:
    try:
        await connection.close(code=code, reason=reason)  # type: ignore[attr-defined]
    except Exception:
        pass


_proxy_instance: DesktopProxy | None = None
_proxy_gateway: SandboxGateway | None = None


def get_desktop_proxy() -> DesktopProxy:
    """Return the process-wide desktop relay, bound to the current gateway."""
    global _proxy_instance, _proxy_gateway
    from yoreni.sandbox.gateway import get_sandbox_gateway

    gateway = get_sandbox_gateway()
    if _proxy_instance is None or _proxy_gateway is not gateway:
        _proxy_instance = DesktopProxy(
            gateway,
            DesktopTokenStore(gateway.config.desktop_token_ttl_s),
        )
        _proxy_gateway = gateway
    return _proxy_instance


def reset_desktop_proxy() -> None:
    global _proxy_instance, _proxy_gateway
    _proxy_instance = None
    _proxy_gateway = None
