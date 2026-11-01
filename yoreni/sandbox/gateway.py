"""The single gateway every agent execution surface goes through."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import posixpath
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from loguru import logger

from yoreni.sandbox.backend import ExecutionHandle, SandboxBackend
from yoreni.sandbox.boxlite_backend import BoxLiteBackend
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.control import ControlStateMachine
from yoreni.sandbox.errors import (
    SandboxControlError,
    SandboxError,
    SandboxPathError,
    SandboxUnavailableError,
)
from yoreni.sandbox.models import (
    ControlOwner,
    ControlSnapshot,
    ExecResult,
    FileInfo,
    SandboxHealth,
    SandboxStatus,
)
from yoreni.sandbox.paths import PathRoots, is_within, normalize_vm_path
from yoreni.sandbox.progress import SandboxProgress
from yoreni.sandbox.provision import DesktopServices
from yoreni.utils.helpers import safe_filename

_gateway_instance: "SandboxGateway | None" = None
# A crashed desktop or browser service is retried in the background; status
# polling drives it so a transient Chromium crash heals without a restart.
_SERVICE_RETRY_S = 60.0
# Health probes cost one VM round trip. The WebUI polls status every few
# seconds, so serve the last measurement and refresh it in the background.
_HEALTH_TTL_S = 5.0
# A pull can begin before a restarted gateway has network; retry the image size
# lookup until it answers so the download keeps its percentage and ETA.
_IMAGE_TOTAL_RETRY_S = 10.0


def set_sandbox_gateway(gateway: "SandboxGateway | None") -> None:
    global _gateway_instance
    _gateway_instance = gateway


def get_sandbox_gateway() -> "SandboxGateway":
    if _gateway_instance is None:
        raise SandboxUnavailableError(
            "The Yoreni sandbox is not configured. Start Yoreni through the gateway "
            "so it can create the execution VM."
        )
    return _gateway_instance


@dataclass(frozen=True)
class ProcessPoll:
    output: str
    done: bool
    exit_code: int | None
    elapsed_s: float
    truncated_chars: int = 0
    finish_reason: str | None = None


class _OutputBuffer:
    """Keep a bounded prefix and tail while counting every character."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._head: list[str] = []
        self._head_chars = 0
        self._tail: deque[str] = deque()
        self._tail_chars = 0
        self._seen = 0

    def append(self, text: str) -> None:
        if not text:
            return
        self._seen += len(text)
        if self._head_chars + len(text) <= self.limit // 2:
            self._head.append(text)
            self._head_chars += len(text)
            return
        self._tail.append(text)
        self._tail_chars += len(text)
        while self._tail_chars > self.limit // 2 and len(self._tail) > 1:
            self._tail_chars -= len(self._tail.popleft())

    def read(self, offset: int) -> str:
        text = "".join(self._head) + "".join(self._tail)
        if offset <= 0:
            return text
        return text[offset:]

    @property
    def length(self) -> int:
        return len("".join(self._head)) + self._tail_chars

    @property
    def total(self) -> int:
        return self._seen

    @property
    def truncated(self) -> int:
        return max(0, self._seen - self.length)


@dataclass
class _Process:
    id: str
    command: str
    cwd: str
    owner_id: str | None
    started_at: float
    handle: ExecutionHandle
    output: _OutputBuffer
    exit_code: int | None = None
    finish_reason: str | None = None
    stdin_closed: bool = False
    stderr_started: bool = False
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)


class VmProcess:
    """A VM process whose raw stdio is bridged to the host.

    Unlike a managed :class:`_Process`, nothing is buffered or re-framed:
    the caller reads the process streams directly, so line-delimited protocols
    such as MCP survive without truncation or stderr interleaving.
    """

    def __init__(self, handle: ExecutionHandle, command: str, cwd: str) -> None:
        self._handle = handle
        self.command = command
        self.cwd = cwd

    def stdout(self) -> AsyncIterator[str | bytes] | None:
        return self._handle.stdout()

    def stderr(self) -> AsyncIterator[str | bytes] | None:
        return self._handle.stderr()

    async def write(self, text: str) -> None:
        stdin = self._handle.stdin()
        if stdin is None:
            raise SandboxError(f"process {self.command!r} has no stdin")
        data = text.encode("utf-8")
        if hasattr(stdin, "send_input"):
            await cast(Any, stdin).send_input(data)
            return
        writer = cast(Any, stdin)
        writer.write(data)
        await writer.drain()

    async def close_stdin(self) -> None:
        stdin = self._handle.stdin()
        if stdin is None:
            return
        with suppress(Exception):
            if hasattr(stdin, "send_input"):
                await cast(Any, stdin).close()
                return
            writer = cast(Any, stdin)
            writer.close()
            await writer.wait_closed()

    async def wait(self) -> int:
        result = await self._handle.wait()
        return int(getattr(result, "exit_code", -1))

    async def kill(self) -> None:
        with suppress(Exception):
            await self._handle.kill()


class SandboxGateway:
    """Own the VM, the workspace policy and the desktop control plane."""

    def __init__(
        self,
        config: SandboxConfig,
        *,
        backend: SandboxBackend | None = None,
        host_workspace: str | None = None,
        host_media_dir: str | None = None,
    ) -> None:
        self.config = config
        self.backend = backend or self._make_backend(config)
        self.control = ControlStateMachine()
        self.roots = PathRoots.for_workspace(
            config.workspace,
            extra_read=(config.media_dir,),
        )
        self._host_workspace = host_workspace.rstrip("/") if host_workspace else None
        self._host_media_dir = host_media_dir.rstrip("/") if host_media_dir else None
        self.desktop = DesktopServices(self.backend, config)
        self.progress = SandboxProgress()
        self._start_lock = asyncio.Lock()
        self._started = False
        self._status_detail = ""
        self._browser_assets_error = ""
        self._service_retry_at = 0.0
        self._service_retry_task: asyncio.Task[None] | None = None
        self._health: SandboxHealth | None = None
        self._health_at = 0.0
        self._health_task: asyncio.Task[None] | None = None
        self._closing = False
        self._processes: dict[str, _Process] = {}
        self._stdio_processes: set["VmProcess"] = set()
        self._viewers: dict[str, str] = {}
        self._desktop_ws_port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._desktop_observed_epoch = -1
        self.control.add_listener(self._control_changed)

    @staticmethod
    def _make_backend(config: SandboxConfig) -> SandboxBackend:
        if config.backend == "boxlite":
            return BoxLiteBackend(config)
        raise SandboxUnavailableError(f"unknown sandbox backend {config.backend!r}")

    # -- lifecycle -----------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Remember the gateway loop so synchronous scanners can call it."""
        self._loop = loop or asyncio.get_running_loop()

    @property
    def loop_bound(self) -> bool:
        return self._loop is not None

    def run_sync(self, coro: Any, *, timeout: float | None = None) -> Any:
        """Run one gateway operation from a worker thread."""
        if self._loop is None:
            raise SandboxUnavailableError("the sandbox gateway loop is not bound")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    @property
    def maps_host_workspace(self) -> bool:
        return self._host_workspace is not None

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        self.bind_loop()
        if not self.config.enabled:
            raise SandboxUnavailableError("the Yoreni sandbox is disabled in settings")
        self._closing = False
        async with self._start_lock:
            if self._started:
                return
            try:
                self.progress.set_stage("image")
                monitor = asyncio.create_task(self._monitor_image_pull())
                try:
                    # The image transfer is network-bound and can outlast any
                    # boot budget; only the boot itself is bounded.
                    await self.backend.prepare()
                    await asyncio.wait_for(
                        self.backend.start(),
                        timeout=self.config.start_timeout_s,
                    )
                finally:
                    monitor.cancel()
                    with suppress(asyncio.CancelledError):
                        await monitor
                self.progress.set_stage("packages")
                await self.desktop.provision()
                await self._install_browser_assets()
                await self._install_bundled_skills()
            except asyncio.TimeoutError as exc:
                self._started = False
                raise SandboxUnavailableError(
                    "the Yoreni VM did not become ready before the configured timeout"
                ) from exc
            except Exception:
                self._started = False
                raise
            # The VM itself is the execution plane. The desktop and browser are
            # services inside it: their failure keeps files and shell usable and
            # is reported per capability instead of taking the whole VM down.
            self._started = True
            self._health = None
        await self.start_services()

    async def start_services(self, *, announce: bool = True) -> None:
        """Start whichever VM services are not running.

        A healthy desktop is never restarted, so a browser problem cannot drop
        the live viewer or the agent's workspace state.
        """
        problems: list[str] = []
        try:
            desktop_ready, browser_ready = await self.desktop.health()
        except Exception:
            desktop_ready = browser_ready = False
        self.progress.set_stage("services")
        if self.config.desktop_enabled and not desktop_ready:
            try:
                await self.desktop.start()
                desktop_ready = True
            except Exception as exc:
                self._desktop_ws_port = None
                problems.append(str(exc))
        if self.config.desktop_enabled and desktop_ready:
            self._desktop_ws_port = await self.backend.forwarded_port(6080)
            # Input authority is owned by the control state machine; push it to
            # x11vnc so a crashed gateway cannot leave the desktop writable.
            await self.desktop.set_view_only(
                self.control.snapshot().owner is not ControlOwner.USER
            )
        if self.config.browser_enabled and not browser_ready:
            if self._browser_assets_error:
                # A transient download or copy failure recovers on the same
                # throttled retry that covers a stopped browser service.
                await self._install_browser_assets()
            try:
                # Chromium outlives the gateway; restart it only when the
                # extension is really gone, so a slow probe cannot kill the tab.
                await self.desktop.start_browser()
            except Exception as exc:
                problems.append(" ".join(filter(None, (self._browser_assets_error, str(exc)))))
        self._status_detail = " ".join(problems)
        # Warm the cache while the caller is still in start-up: the first
        # status poll should not pay for a probe the start already ran.
        self._health = None if problems else await self._probe_health()
        self._service_retry_at = (
            asyncio.get_running_loop().time() + _SERVICE_RETRY_S if problems else 0.0
        )
        for problem in problems:
            if announce:
                logger.error("Yoreni sandbox service unavailable: {}", problem)
            else:
                logger.warning("Yoreni sandbox service still unavailable: {}", problem)

    def _schedule_service_retry(self) -> None:
        if self._service_retry_task is not None or not self._status_detail:
            return
        if self._closing or not self._started:
            return
        loop = asyncio.get_running_loop()
        if loop.time() < self._service_retry_at:
            return

        async def retry() -> None:
            async with self._start_lock:
                if self._started and not self._closing:
                    await self.start_services(announce=False)

        self._service_retry_task = loop.create_task(retry())

        def clear(task: asyncio.Task[None]) -> None:
            self._service_retry_task = None
            if not task.cancelled():
                with suppress(Exception):
                    task.exception()

        self._service_retry_task.add_done_callback(clear)

    async def _monitor_image_pull(self) -> None:
        """Report image download bytes while the VM runtime prepares itself."""
        from yoreni.sandbox import image_manifest

        backend = self.backend
        data_dir = getattr(backend, "data_dir", None)
        candidate = data_dir() if callable(data_dir) else None
        directory = candidate if isinstance(candidate, Path) else None
        if directory is None:
            return
        total: int | None = None
        sizes: dict[str, int] | None = None
        total_task: asyncio.Task[dict[str, int] | None] | None = None
        next_total_at = 0.0
        try:
            while True:
                now = asyncio.get_running_loop().time()
                if total is None:
                    if total_task is None and now >= next_total_at:
                        # Resolving the layer list talks to the registry; keep
                        # it off the loop and report bytes while it is in flight.
                        total_task = asyncio.create_task(
                            asyncio.to_thread(
                                image_manifest.image_layer_sizes, self.config.image
                            )
                        )
                    if total_task is not None and total_task.done():
                        if not total_task.cancelled():
                            with suppress(Exception):
                                sizes = total_task.result()
                                total = sum(sizes.values()) if sizes else None
                        total_task = None
                        # A pull that started before the network returned can
                        # miss the registry; retry instead of downloading blind.
                        next_total_at = now + _IMAGE_TOTAL_RETRY_S
                try:
                    downloaded = await asyncio.to_thread(
                        _layer_bytes, directory / "images" / "layers", sizes
                    )
                except OSError:
                    downloaded = 0
                self.progress.set_bytes(downloaded, total)
                if total and downloaded >= total:
                    # The transfer is done; boxlite now expands the layers.
                    self.progress.set_stage("configuring")
                await asyncio.sleep(0.5)
        finally:
            if total_task is not None:
                total_task.cancel()

    async def _install_browser_assets(self) -> None:
        """Place the BrowserSkill CLI and extension inside the VM.

        Failure here only disables the browser service: files and shell keep
        working, and the startup probe reports the missing capability.
        """
        from yoreni.sandbox import browser_assets

        if not self.config.browser_enabled:
            return
        try:
            self.progress.set_stage("browser")
            await browser_assets.install(self.backend, on_progress=self.progress.set_bytes)
            self._browser_assets_error = ""
        except Exception as exc:
            self._browser_assets_error = f"BrowserSkill is not available in the VM: {exc}"
            logger.warning("Could not install BrowserSkill into the sandbox VM: {}", exc)

    async def _install_bundled_skills(self) -> None:
        """Copy read-only skill assets into the VM workspace on first start."""
        from yoreni.agent.skill_metadata import BUILTIN_SKILLS_DIR

        source = BUILTIN_SKILLS_DIR
        if not source.is_dir():
            return
        target = posixpath.join(self.config.workspace, "skills")
        try:
            await self.backend.copy_in(str(source), target)
        except Exception:
            logger.warning("Could not install bundled skills into the sandbox VM")

    async def ensure_started(self) -> None:
        self.bind_loop()
        if not self._started:
            await self.start()

    async def stop(self) -> None:
        self._closing = True
        self._cancel_health_probe()
        async with self._start_lock:
            self._cancel_service_retry()
            self._health = None
            await self._terminate_all("shutdown")
            await self.backend.stop()
            self._started = False

    async def restart(self) -> None:
        async with self._start_lock:
            self._cancel_service_retry()
            await self._terminate_all("shutdown")
            await self.backend.restart()
            self._started = False
            self._closing = False
        await self.control.force_agent(reason="VM restarted")
        await self.start()

    async def health(self) -> SandboxHealth:
        if not self._started:
            return SandboxHealth(False, False, False, "VM is not started")
        if self._health is None:
            return await self._probe_health()
        now = asyncio.get_running_loop().time()
        if now - self._health_at >= _HEALTH_TTL_S:
            self._schedule_health_probe()
        return self._health

    def _schedule_health_probe(self) -> None:
        if self._health_task is not None or self._closing:
            return
        loop = asyncio.get_running_loop()

        async def probe() -> None:
            if self._started and not self._closing:
                await self._probe_health()

        self._health_task = loop.create_task(probe())

        def clear(task: asyncio.Task[None]) -> None:
            self._health_task = None
            if not task.cancelled():
                with suppress(Exception):
                    task.exception()

        self._health_task.add_done_callback(clear)

    async def _probe_health(self) -> SandboxHealth:
        try:
            raw = await self.backend.status()
            desktop_ready, browser_ready = await self.desktop.health()
            if self._started and not self._status_detail:
                # A service that died after a clean start is restarted by the
                # same throttled retry that covers start-up failures.
                if self.config.desktop_enabled and not desktop_ready:
                    self._status_detail = "the VM desktop stopped responding"
                elif self.config.browser_enabled and not browser_ready:
                    self._status_detail = "the VM browser stopped responding"
            result = SandboxHealth(
                running=raw.state == "running",
                desktop_ready=desktop_ready,
                browser_ready=browser_ready,
                detail=raw.detail,
            )
        except SandboxError as exc:
            result = SandboxHealth(False, False, False, str(exc))
        self._health = result
        self._health_at = asyncio.get_running_loop().time()
        return result

    async def status(self) -> SandboxStatus:
        raw = await self.backend.status()
        health = await self.health() if self._started else raw.health
        if self._started and self._status_detail:
            self._schedule_service_retry()
        return SandboxStatus(
            backend=raw.backend,
            box_id=raw.box_id,
            name=raw.name,
            state=raw.state if self._started else ("stopped" if raw.state == "running" else raw.state),
            workspace=raw.workspace,
            health=health,
            started_at_ms=raw.started_at_ms,
            detail=self._status_detail or raw.detail,
        )

    async def close(self) -> None:
        self._closing = True
        self._cancel_health_probe()
        self._cancel_service_retry()
        self._health = None
        await self._terminate_all("shutdown")
        await self.backend.close()
        self._started = False

    def _cancel_health_probe(self) -> None:
        task = self._health_task
        self._health_task = None
        if task is not None:
            task.cancel()

    def _cancel_service_retry(self) -> None:
        task = self._service_retry_task
        self._service_retry_task = None
        if task is not None:
            task.cancel()

    # -- control -------------------------------------------------------------

    def _control_changed(self, snapshot: ControlSnapshot) -> None:
        if snapshot.owner is ControlOwner.AGENT:
            # Returning control invalidates the desktop: the agent must look
            # again before the next mouse or keyboard action.
            self._desktop_observed_epoch = -1

    async def take_control(self, controller_id: str) -> ControlSnapshot:
        await self.ensure_started()
        snapshot = await self.control.take(
            controller_id,
            grace_s=self.config.takeover_grace_s,
        )
        await self.desktop.set_view_only(False)
        return snapshot

    async def release_control(self, controller_id: str) -> ControlSnapshot:
        # Close the input path before the agent is told it owns the desktop.
        await self.desktop.set_view_only(True)
        return await self.control.release(controller_id, reason="user released control")

    async def viewer_connected(self, controller_id: str) -> None:
        self._viewers[controller_id] = controller_id

    async def viewer_disconnected(self, controller_id: str) -> None:
        self._viewers.pop(controller_id, None)
        if self.control.snapshot().controller_id == controller_id:
            try:
                await self.release_control(controller_id)
            except SandboxControlError:
                await self.control.force_agent(reason="controller disconnected")
                await self.desktop.set_view_only(True)

    @asynccontextmanager
    async def tool_scope(self, name: str) -> AsyncGenerator[None, None]:
        async with self.control.tool_scope(name):
            yield

    def control_snapshot(self) -> ControlSnapshot:
        return self.control.snapshot()

    async def desktop_websocket_url(self) -> str:
        await self.ensure_started()
        if self._desktop_ws_port is None:
            self._desktop_ws_port = await self.backend.forwarded_port(6080)
        if self._desktop_ws_port is None:
            raise SandboxUnavailableError("the VM desktop stream is not available")
        return f"ws://127.0.0.1:{self._desktop_ws_port}/websockify"

    # -- execution -----------------------------------------------------------

    def _host_to_vm(self, path: str) -> str:
        """Map a control-plane workspace path onto the VM workspace.

        The agent still sees the instance's workspace path, which keeps
        project metadata, previews and prompts stable. Execution never touches
        that host directory: the mapped path is what the VM receives.
        """
        normalized = normalize_vm_path(path)
        for host_root, vm_root in (
            (self._host_workspace, self.config.workspace),
            (self._host_media_dir, self.config.media_dir),
        ):
            if not host_root:
                continue
            if is_within(normalized, host_root):
                suffix = normalized[len(host_root):].lstrip("/")
                return posixpath.join(vm_root, suffix) if suffix else vm_root
        return normalized

    def display_path(self, path: str) -> str:
        """Return the path the agent should see and use in VM commands."""
        return self._host_to_vm(path)

    def resolve_path(
        self,
        path: str,
        *,
        cwd: str | None = None,
        write: bool = False,
    ) -> str:
        mapped = self._host_to_vm(path)
        resolved = normalize_vm_path(mapped, cwd=cwd or self.config.workspace)
        if write:
            self.roots.check_write(resolved)
        else:
            self.roots.check_read(resolved)
        return resolved

    async def _safe_path(self, path: str, *, cwd: str | None = None, write: bool) -> str:
        resolved = self.resolve_path(path, cwd=cwd, write=write)
        try:
            actual = await self.backend.realpath(resolved)
        except OSError:
            actual = await self._resolve_missing_tail(resolved)
        if write:
            self.roots.check_write(actual)
        else:
            self.roots.check_read(actual)
        return actual

    async def _resolve_missing_tail(self, path: str) -> str:
        """Resolve the existing ancestor and append the missing tail lexically."""
        tail: list[str] = []
        current = path
        while True:
            parent = posixpath.dirname(current)
            if parent == current:
                return path
            tail.append(posixpath.basename(current))
            current = parent
            try:
                base = await self.backend.realpath(current)
            except OSError:
                continue
            return posixpath.join(base, *reversed(tail))

    async def exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        login: bool = False,
        owner_id: str | None = None,
    ) -> ExecResult:
        await self.ensure_started()
        workdir = self.resolve_path(cwd or self.config.workspace, write=False)
        proc = self._shell(command, login=login)
        await self.control.wait_for_agent()
        try:
            return await self.backend.exec(
                proc,
                cwd=workdir,
                env=env,
                timeout_s=timeout_s or self.config.exec_timeout_s,
            )
        except SandboxUnavailableError:
            self._started = False
            raise

    @staticmethod
    def _shell(command: str | Sequence[str], *, login: bool) -> str | Sequence[str]:
        if isinstance(command, str):
            return ("bash" if login else "sh", "-lc" if login else "-c", command)
        return command

    async def _import_if_needed(self, path: str) -> None:
        """Import a control-plane file or directory the first time it is read.

        Only the instance workspace and the attachment directory are eligible.
        Agent-created files live in the VM and never exist on the host, so this
        is a one-way import of explicitly placed control-plane files.
        """
        vm_path = self._host_to_vm(path)
        if vm_path == normalize_vm_path(path):
            return
        if await self.backend.stat(vm_path) is not None:
            return
        import os

        if not os.path.exists(path):
            return
        await self.backend.mkdir(posixpath.dirname(vm_path))
        await self.backend.copy_in(path, vm_path)

    async def read_file(self, path: str, *, max_bytes: int | None = None) -> bytes:
        await self.ensure_started()
        await self._import_if_needed(path)
        target = await self._safe_path(path, write=False)
        try:
            return await self.backend.read_bytes(target, max_bytes=max_bytes)
        except FileNotFoundError:
            raise
        except SandboxUnavailableError:
            self._started = False
            raise

    async def write_file(
        self,
        path: str,
        data: bytes,
        *,
        mode: int | None = None,
        append: bool = False,
    ) -> str:
        await self.ensure_started()
        target = await self._safe_path(path, write=True)
        await self.backend.write_bytes(target, data, mode=mode, append=append)
        return target

    async def stat(self, path: str) -> FileInfo | None:
        await self.ensure_started()
        await self._import_if_needed(path)
        target = self.resolve_path(path)
        return await self.backend.stat(target)

    async def list_dir(self, path: str) -> list[FileInfo]:
        await self.ensure_started()
        await self._import_if_needed(path)
        target = await self._safe_path(path, write=False)
        return await self.backend.list_dir(target)

    async def walk(self, path: str, *, max_entries: int = 5000) -> list[FileInfo]:
        """Breadth-first walk of a directory inside the workspace."""
        await self.ensure_started()
        await self._import_if_needed(path)
        root = await self._safe_path(path, write=False)
        pending = [root]
        entries: list[FileInfo] = []
        while pending and len(entries) < max_entries:
            current = pending.pop(0)
            try:
                children = await self.backend.list_dir(current)
            except FileNotFoundError:
                continue
            for child in children:
                entries.append(child)
                if child.is_dir and not child.is_symlink:
                    pending.append(child.path)
                if len(entries) >= max_entries:
                    break
        return entries

    async def mkdir(self, path: str) -> str:
        await self.ensure_started()
        target = await self._safe_path(path, write=True)
        await self.backend.mkdir(target)
        return target

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        await self.ensure_started()
        target = await self._safe_path(path, write=True)
        if target == posixpath.normpath(self.config.workspace):
            raise SandboxPathError("refusing to remove the sandbox workspace")
        await self.backend.remove(target, recursive=recursive)

    async def rename(self, source: str, target: str) -> None:
        await self.ensure_started()
        src = await self._safe_path(source, write=True)
        dst = await self._safe_path(target, write=True)
        await self.backend.rename(src, dst)

    async def import_into(self, host_path: str, vm_path: str) -> str:
        """Copy one control-plane file to an exact VM path inside the workspace."""
        await self.ensure_started()
        target = await self._safe_path(vm_path, write=True)
        await self.backend.mkdir(posixpath.dirname(target))
        await self.backend.copy_in(host_path, target)
        return target

    async def import_file(self, host_path: str, *, name: str | None = None) -> str:
        """Import one host file into the VM media directory.

        This is the only host-to-VM path, and it is callable only by the
        control plane (attachment upload), never by an agent tool.
        """
        await self.ensure_started()
        filename = posixpath.basename(name or host_path) or f"import-{uuid.uuid4().hex}"
        target = posixpath.join(self.config.media_dir, filename)
        await self.backend.mkdir(self.config.media_dir)
        await self.backend.copy_in(host_path, target)
        return target

    async def materialize_media(self, path: str, *, subdir: str = "outbound") -> str | None:
        """Copy one VM file into the host media directory and return its host path.

        Control-plane features (WebUI delivery, markdown images, reference
        images) need a host file they can read. Agent-created files only exist
        in the VM, so this is the one place that pulls them across.
        """
        if self._host_media_dir is None:
            return None
        try:
            info = await self.stat(path)
        except (SandboxError, OSError):
            return None
        if info is None or not info.is_file:
            return None
        digest = hashlib.sha256(
            "\0".join((info.path, str(info.size), str(info.mtime_s))).encode("utf-8")
        ).hexdigest()[:20]
        name = safe_filename(Path(info.path).name) or "attachment"
        target = Path(self._host_media_dir) / subdir / f"{digest}-{name}"
        try:
            if not target.is_file() or target.stat().st_size != info.size:
                target.parent.mkdir(parents=True, exist_ok=True)
                await self.export_file(path, str(target))
        except (SandboxError, OSError):
            return None
        return str(target)

    async def export_file(self, vm_path: str, host_path: str) -> None:
        """Export one VM file to a control-plane path (preview/download)."""
        await self.ensure_started()
        target = await self._safe_path(vm_path, write=False)
        await self.backend.copy_out(target, host_path)

    # -- long-running processes ---------------------------------------------

    async def start_process(
        self,
        command: str | Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        login: bool = False,
        owner_id: str | None = None,
    ) -> str:
        await self.ensure_started()
        await self.control.wait_for_agent()
        workdir = self.resolve_path(cwd or self.config.workspace)
        handle = await self.backend.start_exec(
            self._shell(command, login=login),
            cwd=workdir,
            env=env,
            timeout_s=timeout_s or self.config.exec_timeout_s,
        )
        process_id = uuid.uuid4().hex[:12]
        proc = _Process(
            id=process_id,
            command=command if isinstance(command, str) else " ".join(map(str, command)),
            cwd=workdir,
            owner_id=owner_id,
            started_at=time.monotonic(),
            handle=handle,
            output=_OutputBuffer(self.config.exec_max_output_chars),
        )
        self._processes[process_id] = proc
        proc.tasks = [
            asyncio.create_task(self._pump(proc, handle.stdout(), "stdout")),
            asyncio.create_task(self._pump(proc, handle.stderr(), "stderr")),
            asyncio.create_task(self._watch(proc)),
        ]
        return process_id

    async def _pump(
        self,
        proc: _Process,
        stream: AsyncIterator[str | bytes] | None,
        label: str,
    ) -> None:
        if stream is None:
            return
        try:
            async for line in stream:
                text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                if label == "stderr" and text and not proc.stderr_started:
                    proc.stderr_started = True
                    text = "STDERR:\n" + text
                if label == "stderr" and text and not text.endswith("\n"):
                    text += "\n"
                proc.output.append(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Process {} {} stream ended with an error", proc.id, label)

    async def _watch(self, proc: _Process) -> None:
        try:
            result = await proc.handle.wait()
            proc.exit_code = int(getattr(result, "exit_code", -1))
            proc.finish_reason = proc.finish_reason or "exit"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            proc.finish_reason = "execution_error"
            proc.output.append(f"\n[process error: {exc}]\n")

    def poll_process(self, process_id: str, *, offset: int = 0) -> ProcessPoll:
        proc = self._processes.get(process_id)
        if proc is None:
            raise KeyError(process_id)
        return ProcessPoll(
            output=proc.output.read(offset),
            done=proc.exit_code is not None or proc.finish_reason in {
                "cancelled", "execution_error",
            },
            exit_code=proc.exit_code,
            elapsed_s=time.monotonic() - proc.started_at,
            truncated_chars=proc.output.truncated,
            finish_reason=proc.finish_reason,
        )

    async def write_process(self, process_id: str, text: str) -> None:
        proc = self._require_process(process_id)
        stdin = proc.handle.stdin()
        if stdin is None or proc.stdin_closed:
            raise SandboxError("process stdin is closed")
        data = text.encode("utf-8")
        if hasattr(stdin, "send_input"):
            await cast(Any, stdin).send_input(data)
            return
        writer = cast(Any, stdin)
        writer.write(data)
        await writer.drain()

    async def close_process_stdin(self, process_id: str) -> None:
        proc = self._require_process(process_id)
        stdin = proc.handle.stdin()
        if stdin is None or proc.stdin_closed:
            return
        proc.stdin_closed = True
        if hasattr(stdin, "send_input"):
            await cast(Any, stdin).close()
            return
        writer = cast(Any, stdin)
        with suppress(Exception):
            writer.close()
            await writer.wait_closed()

    async def start_stdio_process(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> VmProcess:
        """Start an argv command in the VM and hand its raw stdio to the caller.

        Connecting is not a tool call, so it does not wait for desktop control:
        the tool scope around each MCP tool call owns that boundary.
        """
        await self.ensure_started()
        workdir = self.resolve_path(cwd or self.config.workspace)
        handle = await self.backend.start_exec(command, cwd=workdir, env=env)
        process = VmProcess(handle, " ".join(map(str, command)), workdir)
        self._stdio_processes.add(process)
        return process

    async def close_stdio_process(self, process: VmProcess) -> None:
        self._stdio_processes.discard(process)
        await process.kill()

    async def kill_process(self, process_id: str, *, reason: str = "cancelled") -> bool:
        proc = self._processes.get(process_id)
        if proc is None:
            return False
        proc.finish_reason = reason
        with suppress(Exception):
            await proc.handle.kill()
        for task in proc.tasks:
            task.cancel()
        return True

    async def list_processes(self) -> list[dict[str, Any]]:
        return [
            {
                "id": proc.id,
                "command": proc.command,
                "cwd": proc.cwd,
                "elapsed_s": time.monotonic() - proc.started_at,
                "exit_code": proc.exit_code,
                "finish_reason": proc.finish_reason,
                "owner_id": proc.owner_id,
            }
            for proc in self._processes.values()
        ]

    async def terminate_by_owner(self, owner_id: str) -> int:
        killed = 0
        for proc in list(self._processes.values()):
            if proc.owner_id == owner_id and proc.exit_code is None:
                if await self.kill_process(proc.id, reason="owner_closed"):
                    killed += 1
        return killed

    async def _terminate_all(self, reason: str) -> None:
        for proc in list(self._processes.values()):
            if proc.exit_code is None:
                await self.kill_process(proc.id, reason=reason)
        for bridged in list(self._stdio_processes):
            self._stdio_processes.discard(bridged)
            await bridged.kill()

    def _require_process(self, process_id: str) -> _Process:
        proc = self._processes.get(process_id)
        if proc is None:
            raise KeyError(process_id)
        return proc

    # -- desktop -------------------------------------------------------------

    async def desktop_screenshot(self) -> bytes:
        await self.ensure_started()
        self.control.ensure_agent("desktop screenshot")
        self._desktop_observed_epoch = self.control.snapshot().epoch
        shot = "/tmp/yoreni-desktop.png"
        result = await self.backend.exec(
            ("scrot", "-o", "-z", shot),
            cwd=self.config.workspace,
            env={"DISPLAY": self.config.display},
            timeout_s=20,
        )
        if result.exit_code != 0:
            raise SandboxError(f"desktop screenshot failed: {result.stderr.strip()}")
        encoded = await self.backend.exec(
            ("base64", "-w0", "--", shot),
            cwd="/",
            timeout_s=30,
        )
        if encoded.exit_code != 0:
            raise SandboxError("desktop screenshot could not be read")
        return base64.b64decode(encoded.stdout.strip())

    def _require_desktop_observation(self, action: str) -> None:
        if self._desktop_observed_epoch != self.control.snapshot().epoch:
            raise SandboxControlError(
                f"{action} requires a fresh desktop screenshot after control changed"
            )

    async def desktop_mouse(
        self,
        action: str,
        *,
        x: int | None = None,
        y: int | None = None,
        end_x: int | None = None,
        end_y: int | None = None,
        button: int = 1,
        amount: int = 3,
    ) -> None:
        await self.ensure_started()
        self.control.ensure_agent("desktop mouse")
        self._require_desktop_observation("desktop mouse")
        commands: list[str] = []
        if action == "move" and x is not None and y is not None:
            commands = ["mousemove", str(x), str(y)]
        elif action == "click" and x is not None and y is not None:
            commands = ["mousemove", str(x), str(y), "click", str(button)]
        elif action == "drag" and None not in (x, y, end_x, end_y):
            commands = [
                "mousemove", str(x), str(y), "mousedown", str(button),
                "mousemove", str(end_x), str(end_y), "mouseup", str(button),
            ]
        elif action == "scroll" and x is not None and y is not None:
            wheel = {4: "4", 5: "5", 6: "6", 7: "7"}.get(button, "5")
            commands = ["mousemove", str(x), str(y), "click", "--repeat", str(amount), wheel]
        else:
            raise ValueError("invalid mouse action")
        await self._xdotool(commands)

    async def desktop_key(self, keys: str) -> None:
        await self.ensure_started()
        self.control.ensure_agent("desktop keyboard")
        self._require_desktop_observation("desktop keyboard")
        await self._xdotool(["key", "--clearmodifiers", keys])

    async def desktop_type(self, text: str) -> None:
        await self.ensure_started()
        self.control.ensure_agent("desktop keyboard")
        self._require_desktop_observation("desktop keyboard")
        await self._xdotool(["type", "--clearmodifiers", "--delay", "12", "--", text])

    async def desktop_clipboard(self, *, text: str | None = None) -> str:
        await self.ensure_started()
        self.control.ensure_agent("desktop clipboard")
        self._require_desktop_observation("desktop clipboard")
        try:
            if text is None:
                result = await asyncio.wait_for(
                    self.backend.exec(
                        ("xclip", "-selection", "clipboard", "-o"),
                        cwd=self.config.workspace,
                        env={"DISPLAY": self.config.display},
                        timeout_s=5,
                    ),
                    timeout=8,
                )
                return result.stdout if result.exit_code == 0 else ""
            # xclip stays in the foreground for as long as it owns the
            # selection, so the writer must be detached from the exec.
            await asyncio.wait_for(
                self.backend.exec(
                    (
                        "sh",
                        "-c",
                        'printf %s "$YORENI_CLIP" | setsid xclip -selection '
                        "clipboard -quiet >/dev/null 2>&1 &",
                    ),
                    cwd=self.config.workspace,
                    env={"DISPLAY": self.config.display, "YORENI_CLIP": text},
                    timeout_s=5,
                ),
                timeout=8,
            )
        except asyncio.TimeoutError:
            logger.warning("Desktop clipboard operation did not finish")
        return ""

    async def _xdotool(self, args: Sequence[str]) -> None:
        result = await self.backend.exec(
            ("xdotool", *args),
            cwd=self.config.workspace,
            env={"DISPLAY": self.config.display},
            timeout_s=20,
        )
        if result.exit_code != 0:
            raise SandboxError(
                f"desktop input failed: {(result.stderr or result.stdout).strip()[:300]}"
            )


def _layer_bytes(root: Path, sizes: Mapping[str, int] | None) -> int:
    """Sum each image layer once, even while a retry leaves two files behind.

    The runtime downloads a layer to ``sha256-<digest>.<id>.downloading`` and
    keeps the verified copy as ``sha256-<digest>.tar.gz``. An aborted attempt
    leaves its temporary file in place, so adding every file would count one
    layer twice and report the image as downloaded before it is.
    """
    per_digest: dict[str, int] = {}
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        digest = path.name.removeprefix("sha256-").split(".", 1)[0]
        if sizes is not None:
            expected = sizes.get(digest)
            if expected is None:
                continue
            size = min(size, expected)
        per_digest[digest] = max(per_digest.get(digest, 0), size)
    return sum(per_digest.values())
