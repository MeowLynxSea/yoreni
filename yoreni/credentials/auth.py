"""Structured authentication requests and the run-resumption handshake.

A request records *what* the agent needs (connector, method, fields, purpose)
and who asked for it (run, allowed tools, allowed domains). The submitted
values travel only through :meth:`CredentialManager.submit_auth_request`; they
are never part of an agent message, a tool result, or an elicitation response.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yoreni.credentials.models import AuthMethod


class AuthRequestStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class AuthRequest:
    id: str
    connector_id: str
    method: AuthMethod
    fields: tuple[str, ...]
    purpose: str
    run_id: str | None
    status: AuthRequestStatus = AuthRequestStatus.PENDING
    account_id: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    allowed_tools: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    error: str | None = None

    @property
    def expired(self) -> bool:
        return self.status is AuthRequestStatus.PENDING and self.expires_at <= time.time()

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "connector_id": self.connector_id,
            "method": self.method.value,
            "fields": list(self.fields),
            "purpose": self.purpose,
            "run_id": self.run_id,
            "status": self.status.value,
            "account_id": self.account_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "allowed_tools": list(self.allowed_tools),
            "allowed_domains": list(self.allowed_domains),
            "error": self.error,
        }


class AuthRequestManager:
    """Tracks pending authentication requests and their waiting runs."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 600,
        notify: Callable[[AuthRequest], None] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._notify = notify
        self._requests: dict[str, AuthRequest] = {}
        self._waiters: dict[str, asyncio.Future[AuthRequest]] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def create(
        self,
        request_id: str,
        *,
        connector_id: str,
        method: AuthMethod,
        fields: tuple[str, ...],
        purpose: str,
        run_id: str | None,
        allowed_tools: tuple[str, ...],
        allowed_domains: tuple[str, ...],
        ttl_seconds: float | None = None,
    ) -> AuthRequest:
        now = time.time()
        request = AuthRequest(
            id=request_id,
            connector_id=connector_id,
            method=method,
            fields=fields,
            purpose=purpose,
            run_id=run_id,
            created_at=now,
            expires_at=now + (ttl_seconds if ttl_seconds is not None else self._ttl),
            allowed_tools=allowed_tools,
            allowed_domains=allowed_domains,
        )
        loop = asyncio.get_running_loop()
        with self._guard:
            self._expire_locked()
            self._requests[request_id] = request
            self._waiters[request_id] = loop.create_future()
            self._loops[request_id] = loop
            self._locks[request_id] = threading.Lock()
        self._emit(request)
        return request

    def get(self, request_id: str) -> AuthRequest | None:
        with self._guard:
            self._expire_locked()
            return self._requests.get(request_id)

    def set_notify(self, notify: Callable[[AuthRequest], None] | None) -> None:
        """Install the UI notification sink after construction."""
        self._notify = notify

    @property
    def has_listener(self) -> bool:
        return self._notify is not None

    def pending(self) -> list[AuthRequest]:
        with self._guard:
            self._expire_locked()
            return [request for request in self._requests.values() if request.status is AuthRequestStatus.PENDING]

    def lock(self, request_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(request_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[request_id] = lock
            return lock

    def complete(self, request_id: str, account_id: str) -> None:
        with self._guard:
            request = self._requests.get(request_id)
            if request is None or request.status is not AuthRequestStatus.PENDING:
                return
            request.status = AuthRequestStatus.COMPLETED
            request.account_id = account_id
            self._settle_locked(request_id, request)
        self._emit(request)

    def cancel(self, request_id: str) -> AuthRequest | None:
        with self._guard:
            request = self._requests.get(request_id)
            if request is None:
                return None
            if request.status is AuthRequestStatus.PENDING:
                request.status = AuthRequestStatus.CANCELLED
            self._settle_locked(request_id, request)
        self._emit(request)
        return request

    def fail(self, request_id: str, message: str) -> None:
        with self._guard:
            request = self._requests.get(request_id)
            if request is None or request.status is not AuthRequestStatus.PENDING:
                return
            request.status = AuthRequestStatus.FAILED
            request.error = message
            self._settle_locked(request_id, request)
        self._emit(request)

    def _settle_locked(self, request_id: str, request: AuthRequest) -> None:
        """Resolve the waiting Run from any thread that submits the card."""
        waiter = self._waiters.pop(request_id, None)
        loop = self._loops.pop(request_id, None)
        if waiter is None or waiter.done():
            return
        if loop is None or loop.is_closed():
            return

        def resolve() -> None:
            if not waiter.done():
                waiter.set_result(request)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            resolve()
        else:
            loop.call_soon_threadsafe(resolve)

    async def wait(self, request_id: str, timeout: float) -> AuthRequest:
        waiter = self._waiters.get(request_id)
        if waiter is None:
            request = self.get(request_id)
            if request is None:
                raise KeyError(request_id)
            return request
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout)
        except asyncio.TimeoutError:
            return self.get(request_id) or AuthRequest(
                id=request_id,
                connector_id="",
                method=AuthMethod.NONE,
                fields=(),
                purpose="",
                run_id=None,
                status=AuthRequestStatus.EXPIRED,
            )

    def _expire_locked(self) -> None:
        now = time.time()
        for request_id, request in list(self._requests.items()):
            if request.status is not AuthRequestStatus.PENDING:
                continue
            if request.expires_at <= now:
                request.status = AuthRequestStatus.EXPIRED
                self._settle_locked(request_id, request)

    def _emit(self, request: AuthRequest) -> None:
        if self._notify is None:
            return
        try:
            self._notify(request)
        except Exception:
            # A UI notification must never change the authentication outcome.
            import logging

            logging.getLogger(__name__).exception("auth request notification failed")
