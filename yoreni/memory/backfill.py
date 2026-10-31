"""Durable progress for re-indexing memory after the embedding endpoint changes.

The vector store keys every row by ``model:dimensions[:multimodal]``, so a new
embedding model turns the whole index into a gap. This controller turns that
gap into one resumable task: it persists its own state in ``memory_meta``,
recomputes the remaining work from the store instead of trusting a counter, and
survives restarts without a schema migration.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from yoreni.memory.embeddings import embedding_index_key
from yoreni.memory.models import utc_now

if TYPE_CHECKING:
    from yoreni.memory.service import MemoryService

STATE_KEY = "embedding_backfill"
RUNNING = "running"
PAUSED = "paused"

_BATCH = 64
_BUSY_SLEEP_S = 2.0
_MAX_RETRY_SLEEP_S = 60.0
_IDLE_SLEEP_S = 60.0


class EmbeddingBackfill:
    """One resumable "the memory index needs rebuilding" task per workspace."""

    def __init__(self, service: "MemoryService") -> None:
        self.service = service
        # Set by the gateway so progress reaches the WebUI; None elsewhere.
        self.on_change: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self.reconcile()
        await self._notify()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_loop(), name="yoreni-memory-backfill")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_loop(self) -> None:
        idle = 0
        while True:
            try:
                # Clear before reading state so a pause/resume that lands while
                # we decide cannot be swallowed by the wait below.
                self._wake.clear()
                state = self._load()
                live_key = self._live_key()
                if (
                    state is None
                    or live_key is None
                    or state.get("index_key") != live_key
                    or state.get("status") != RUNNING
                ):
                    idle = 0
                    await self._sleep_until_woken(_IDLE_SLEEP_S)
                    continue
                embedded = await self.step()
                if embedded == 0:
                    # Nothing moved: an unreachable endpoint must not turn into
                    # a request every two seconds.
                    idle += 1
                    await asyncio.sleep(min(_BUSY_SLEEP_S * idle, _MAX_RETRY_SLEEP_S))
                else:
                    idle = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a failing store must not kill the loop
                logger.opt(exception=exc).warning("memory update loop failed: {}", exc)
                await asyncio.sleep(_BUSY_SLEEP_S)

    async def _sleep_until_woken(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except TimeoutError:
            pass

    # -- task state ----------------------------------------------------------

    def reconcile(self) -> dict[str, Any]:
        """Align the stored task with the configured index; drop a stale one."""
        key = self._live_key()
        state = self._load()
        if key is None or state is None or state.get("index_key") != key:
            if state is not None:
                self._clear()
            if key is None:
                self._publish()
                return self.snapshot()
            remaining = self.service.store.count_missing_vectors(key)
            if remaining == 0:
                self._publish()
                return self.snapshot()
            self._save({
                "index_key": key,
                "model": self._model(),
                "status": RUNNING,
                "done": 0,
                "started_at": utc_now(),
                "active_ms": 0,
                "updated_at": utc_now(),
            })
            self._wake.set()
            self._publish()
            return self.snapshot()

        if self.service.store.count_missing_vectors(key) == 0:
            self._clear()
            self._publish()
        else:
            self._wake.set()
        return self.snapshot()

    async def pause(self) -> dict[str, Any]:
        state = self._load()
        if state is not None and state.get("status") != PAUSED:
            state["status"] = PAUSED
            state["updated_at"] = utc_now()
            self._save(state)
        self._wake.set()
        await self._notify()
        return self.snapshot()

    async def resume(self) -> dict[str, Any]:
        state = self._load()
        if state is not None and state.get("status") != RUNNING:
            state["status"] = RUNNING
            state["updated_at"] = utc_now()
            self._save(state)
        self._wake.set()
        await self._notify()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        key = self._live_key()
        state = self._load()
        if key is None or state is None or state.get("index_key") != key:
            return {"active": False}
        remaining = max(0, self.service.store.count_missing_vectors(key))
        if remaining == 0:
            return {"active": False}
        done = max(0, int(state.get("done", 0)))
        paused = state.get("status") == PAUSED
        eta_ms: int | None = None
        if not paused and done > 0:
            active_ms = max(0, int(state.get("active_ms", 0)))
            if active_ms > 0:
                eta_ms = int(remaining * active_ms / done)
        return {
            "active": True,
            "status": PAUSED if paused else RUNNING,
            "model": str(state.get("model") or ""),
            "done": done,
            "total": done + remaining,
            "remaining": remaining,
            "eta_ms": eta_ms,
        }

    # -- work ----------------------------------------------------------------

    async def step(self) -> int:
        """Embed one batch; returns how many facts gained a vector."""
        async with self._lock:
            if self._load() is None:
                # A stray gap (an embedding failure outside a known task) still
                # deserves the same visible, pausable task.
                self.reconcile()
            state = self._load()
            key = self._live_key()
            if (
                state is None
                or key is None
                or state.get("index_key") != key
                or state.get("status") != RUNNING
            ):
                return 0
            if self.service.store.count_missing_vectors(key) == 0:
                self._clear()
                await self._notify()
                return 0
            started = time.monotonic()
            try:
                embedded = await self.service.embed_missing(limit=_BATCH)
            except Exception as exc:
                # A failed batch keeps the task pending; the next tick retries.
                logger.opt(exception=exc).warning("memory update batch failed: {}", exc)
                return 0
            elapsed_ms = int((time.monotonic() - started) * 1000)
            state = self._load() or state
            state["done"] = max(0, int(state.get("done", 0))) + embedded
            state["active_ms"] = max(0, int(state.get("active_ms", 0))) + elapsed_ms
            state["updated_at"] = utc_now()
            self._save(state)
            if self.service.store.count_missing_vectors(key) == 0:
                self._clear()
            await self._notify()
            return embedded

    # -- persistence ---------------------------------------------------------

    def _live_key(self) -> str | None:
        embedder = self.service.embedder
        if embedder is None:
            return None
        return embedding_index_key(embedder.settings)

    def _model(self) -> str:
        embedder = self.service.embedder
        if embedder is None:
            return ""
        return str(getattr(embedder.settings, "model", "") or "")

    def _load(self) -> dict[str, Any] | None:
        raw = self.service.store.meta_get(STATE_KEY)
        if not raw:
            return None
        try:
            state = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(state, dict) or not isinstance(state.get("index_key"), str):
            return None
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.service.store.meta_set(STATE_KEY, json.dumps(state, sort_keys=True))

    def _clear(self) -> None:
        self.service.store.meta_delete(STATE_KEY)

    def _publish(self) -> None:
        """Notify from a synchronous caller without owning the event loop."""
        if self.on_change is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._notify())

    async def _notify(self) -> None:
        if self.on_change is None:
            return
        try:
            await self.on_change(self.snapshot())
        except Exception as exc:
            logger.opt(exception=exc).warning("memory update notification failed: {}", exc)


__all__ = ["EmbeddingBackfill", "PAUSED", "RUNNING", "STATE_KEY"]
