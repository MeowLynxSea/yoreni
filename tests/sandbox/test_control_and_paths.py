"""Contracts for the execution plane, independent of a hypervisor."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.control import ControlStateMachine
from yoreni.sandbox.desktop_proxy import DesktopTokenStore
from yoreni.sandbox.errors import SandboxControlError, SandboxPathError
from yoreni.sandbox.gateway import SandboxGateway
from yoreni.sandbox.models import ControlOwner, ExecResult, FileInfo, SandboxHealth, SandboxStatus


class RecordingBackend:
    name = "recording"

    def __init__(self) -> None:
        self.commands: list[tuple[Any, str]] = []
        self.files: dict[str, bytes] = {}

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def restart(self) -> None: ...

    async def status(self) -> SandboxStatus:
        return SandboxStatus(
            backend=self.name,
            box_id="box",
            name="test",
            state="running",
            workspace="/workspace",
            health=SandboxHealth(True, False, False),
        )

    async def exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
    ) -> ExecResult:
        del env, timeout_s, user
        self.commands.append((command, cwd))
        return ExecResult(command=str(command), exit_code=0, stdout="ok", stderr="")

    async def start_exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
        tty: bool = False,
    ) -> Any:
        del env, timeout_s, user, tty
        self.commands.append((command, cwd))
        raise AssertionError("start_exec is not used in this test")

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        data = self.files[path]
        return data[:max_bytes] if max_bytes else data

    async def write_bytes(
        self, path: str, data: bytes, *, mode: int | None = None, append: bool = False,
    ) -> None:
        del mode
        self.files[path] = self.files.get(path, b"") + data if append else data

    async def list_dir(self, path: str) -> list[FileInfo]:
        raise FileNotFoundError(path)

    async def stat(self, path: str) -> FileInfo | None:
        if path not in self.files:
            return None
        return FileInfo(path, False, True, False, len(self.files[path]), 0o644, 0.0)

    async def realpath(self, path: str) -> str:
        return path

    async def mkdir(self, path: str, *, parents: bool = True) -> None: ...
    async def remove(self, path: str, *, recursive: bool = False) -> None: ...
    async def rename(self, source: str, target: str) -> None: ...
    async def forwarded_port(self, guest_port: int) -> int | None:
        return None

    def host_staging_dir(self) -> str:
        return "/tmp"

    async def copy_in(self, host_path: str, vm_path: str) -> None:
        self.files[vm_path] = b"imported"

    async def copy_out(self, vm_path: str, host_path: str) -> None:
        del vm_path, host_path

    async def close(self) -> None: ...


class _PortBackend(RecordingBackend):
    async def forwarded_port(self, guest_port: int) -> int | None:
        return guest_port


def _gateway(backend: RecordingBackend | None = None, **kwargs: Any) -> SandboxGateway:
    options: dict[str, Any] = {
        "workspace": "/workspace",
        "media_dir": "/workspace/.yoreni/media",
        "desktop_enabled": False,
        "browser_enabled": False,
    }
    options.update(kwargs)
    config = SandboxConfig(enabled=True, backend="recording", **options)
    gateway = SandboxGateway(
        config,
        backend=backend or RecordingBackend(),
        host_workspace="/host/workspace",
        host_media_dir="/host/media",
    )
    gateway._started = True
    return gateway


def test_host_workspace_maps_into_the_vm() -> None:
    gateway = _gateway()
    assert gateway.resolve_path("/host/workspace/a.txt") == "/workspace/a.txt"
    assert gateway.resolve_path("/host/media/photo.png") == "/workspace/.yoreni/media/photo.png"
    with pytest.raises(SandboxPathError):
        gateway.resolve_path("/etc/passwd")


async def test_file_writes_use_the_mapped_vm_path() -> None:
    gateway = _gateway()
    backend = gateway.backend
    assert isinstance(backend, RecordingBackend)

    await gateway.write_file("/host/workspace/src/a.py", b"print('x')")

    assert backend.files == {"/workspace/src/a.py": b"print('x')"}


async def test_exec_rejects_a_cwd_outside_the_workspace() -> None:
    gateway = _gateway()
    with pytest.raises(SandboxPathError):
        await gateway.exec("pwd", cwd="/etc")


async def test_control_takeover_blocks_agent_actions_until_release() -> None:
    gateway = _gateway()
    snapshot = await gateway.control.take("viewer-1", grace_s=0)
    assert snapshot.owner is ControlOwner.USER
    with pytest.raises(SandboxControlError):
        gateway.control.ensure_agent("browser click")

    returned = await gateway.release_control("viewer-1")
    assert returned.owner is ControlOwner.AGENT
    gateway.control.ensure_agent("browser click")


async def test_only_the_controller_can_release() -> None:
    gateway = _gateway()
    await gateway.control.take("viewer-1", grace_s=0)
    with pytest.raises(SandboxControlError):
        await gateway.control.release("viewer-2")


async def test_takeover_waits_for_the_current_tool_to_finish() -> None:
    gateway = _gateway()
    finished = asyncio.Event()

    async def tool() -> None:
        async with gateway.tool_scope("exec"):
            await asyncio.sleep(0.1)
            finished.set()

    task = asyncio.create_task(tool())
    await asyncio.sleep(0.01)
    await gateway.control.take("viewer-1", grace_s=1)
    assert finished.is_set()
    await task


def test_desktop_tickets_are_single_use_and_bind_the_controller() -> None:
    store = DesktopTokenStore(ttl_s=60)
    ticket = store.issue()
    assert store.consume(ticket) is True
    assert store.consume(ticket) is False
    assert store.is_controller(ticket) is True
    store.revoke(ticket)
    assert store.is_controller(ticket) is False


async def test_controller_disconnect_returns_input_to_the_agent() -> None:
    gateway = _gateway()
    await gateway.control.take("viewer-1", grace_s=0)
    await gateway.viewer_disconnected("viewer-1")
    assert gateway.control_snapshot().owner is ControlOwner.AGENT


class EmptyControl(ControlStateMachine):
    pass


def test_control_epoch_advances_on_every_transition() -> None:
    async def scenario() -> None:
        control = EmptyControl()
        first = await control.take("v", grace_s=0)
        second = await control.release("v")
        third = await control.take("v", grace_s=0)
        assert first.epoch < second.epoch < third.epoch

    asyncio.run(scenario())


class _StubViewer:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> Any:
        await asyncio.sleep(3600)
        raise StopAsyncIteration

    async def send(self, message: Any) -> None:
        self.sent.append(message)


def test_desktop_relay_asks_websockify_for_binary_frames(monkeypatch) -> None:
    from yoreni.sandbox import desktop_proxy

    captured: dict[str, Any] = {}

    class _Upstream:
        async def __aenter__(self) -> Any:
            return _Upstream()

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

        async def send(self, message: Any) -> None:
            raise AssertionError("nothing should be relayed upstream")

    def fake_connect(url: str, **kwargs: Any) -> _Upstream:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Upstream()

    monkeypatch.setattr(desktop_proxy, "ws_connect", fake_connect)
    proxy = desktop_proxy.DesktopProxy(_gateway(_PortBackend()), DesktopTokenStore(ttl_s=60))

    asyncio.run(proxy.serve(_StubViewer(), "viewer-1"))

    assert captured["url"].endswith("/websockify")
    assert captured["kwargs"]["subprotocols"] == ["binary"]


async def test_a_failed_desktop_service_keeps_the_vm_usable(monkeypatch) -> None:
    from yoreni.sandbox.errors import SandboxUnavailableError

    gateway = _gateway(_PortBackend(), desktop_enabled=True)

    async def broken() -> None:
        raise SandboxUnavailableError("x11vnc is not running")

    async def unhealthy() -> tuple[bool, bool]:
        return False, False

    gateway._started = False
    monkeypatch.setattr(gateway.desktop, "health", unhealthy)
    monkeypatch.setattr(gateway.desktop, "start", broken)

    await gateway.start()

    assert gateway.started
    assert "x11vnc is not running" in gateway._status_detail
    assert gateway.resolve_path("/host/workspace/a.txt") == "/workspace/a.txt"


async def test_a_failed_browser_service_keeps_the_vm_usable(monkeypatch) -> None:
    from yoreni.sandbox.errors import SandboxUnavailableError

    gateway = _gateway(_PortBackend(), browser_enabled=True)

    async def broken(_cdp_url: str) -> None:
        raise SandboxUnavailableError("Chromium did not open its CDP endpoint")

    gateway._started = False
    monkeypatch.setattr(gateway.browser, "connect", broken)

    await gateway.start()

    assert gateway.started
    assert not gateway.browser.connected
    assert "CDP endpoint" in gateway._status_detail


async def test_status_reuses_a_recent_health_probe() -> None:
    backend = _PortBackend()
    gateway = _gateway(backend, desktop_enabled=True, browser_enabled=True)

    await gateway.health()
    await gateway.status()
    await gateway.status()

    probes = [cmd for cmd, _cwd in backend.commands if "create_connection" in str(cmd)]
    assert len(probes) == 1
