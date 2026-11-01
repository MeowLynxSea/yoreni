"""Backend contract for the persistent execution VM."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol

from yoreni.sandbox.models import ExecResult, FileInfo, SandboxStatus


class ExecutionHandle(Protocol):
    """A live process inside the VM."""

    def stdout(self) -> AsyncIterator[str | bytes] | None: ...

    def stderr(self) -> AsyncIterator[str | bytes] | None: ...

    def stdin(self) -> object | None: ...

    async def wait(self) -> object: ...

    async def kill(self) -> None: ...


class SandboxBackend(Protocol):
    """Lifecycle and raw execution primitives implemented by a VM runtime."""

    name: str

    async def prepare(self) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def restart(self) -> None: ...

    async def status(self) -> SandboxStatus: ...

    async def exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
    ) -> ExecResult: ...

    async def start_exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
        tty: bool = False,
    ) -> ExecutionHandle: ...

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes: ...

    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        mode: int | None = None,
        append: bool = False,
    ) -> None: ...

    async def list_dir(self, path: str) -> list[FileInfo]: ...

    async def stat(self, path: str) -> FileInfo | None: ...

    async def realpath(self, path: str) -> str: ...

    async def mkdir(self, path: str, *, parents: bool = True) -> None: ...

    async def remove(self, path: str, *, recursive: bool = False) -> None: ...

    async def rename(self, source: str, target: str) -> None: ...

    async def forwarded_port(self, guest_port: int) -> int | None: ...

    def host_staging_dir(self) -> str: ...

    async def copy_in(self, host_path: str, vm_path: str) -> None: ...

    async def copy_out(self, vm_path: str, host_path: str) -> None: ...

    async def close(self) -> None: ...


class BackendFactory(Protocol):
    def __call__(self) -> SandboxBackend: ...


def split_command(command: str | Sequence[str]) -> tuple[str, list[str]]:
    """Split a command string for the VM shell or normalize an argv list."""
    if isinstance(command, str):
        return "sh", ["-lc", command]
    if not command:
        raise ValueError("command must not be empty")
    return command[0], list(command[1:])
