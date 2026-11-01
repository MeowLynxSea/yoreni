"""Agent tools fail closed when the VM is unavailable."""

from __future__ import annotations

from typing import Any

import pytest

from yoreni.agent.tools.shell import ExecTool
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.gateway import SandboxGateway, set_sandbox_gateway


class DownBackend:
    name = "down"

    async def prepare(self) -> None:
        raise SandboxUnavailableError("no virtualization")

    async def start(self) -> None:
        raise SandboxUnavailableError("no virtualization")

    async def stop(self) -> None: ...
    async def restart(self) -> None: ...
    async def status(self) -> Any:
        raise SandboxUnavailableError("no virtualization")

    async def exec(self, *args: Any, **kwargs: Any) -> Any:
        raise SandboxUnavailableError("no virtualization")

    async def start_exec(self, *args: Any, **kwargs: Any) -> Any:
        raise SandboxUnavailableError("no virtualization")

    async def read_bytes(self, *args: Any, **kwargs: Any) -> bytes:
        raise SandboxUnavailableError("no virtualization")

    async def write_bytes(self, *args: Any, **kwargs: Any) -> None:
        raise SandboxUnavailableError("no virtualization")

    async def list_dir(self, *args: Any, **kwargs: Any) -> Any:
        raise SandboxUnavailableError("no virtualization")

    async def stat(self, *args: Any, **kwargs: Any) -> Any:
        raise SandboxUnavailableError("no virtualization")

    async def realpath(self, *args: Any, **kwargs: Any) -> str:
        raise SandboxUnavailableError("no virtualization")

    async def mkdir(self, *args: Any, **kwargs: Any) -> None: ...
    async def remove(self, *args: Any, **kwargs: Any) -> None: ...
    async def rename(self, *args: Any, **kwargs: Any) -> None: ...
    async def forwarded_port(self, *args: Any, **kwargs: Any) -> int | None:
        return None

    def host_staging_dir(self) -> str:
        return "/tmp"

    async def copy_in(self, *args: Any, **kwargs: Any) -> None:
        raise SandboxUnavailableError("no virtualization")

    async def copy_out(self, *args: Any, **kwargs: Any) -> None:
        raise SandboxUnavailableError("no virtualization")

    async def close(self) -> None: ...


@pytest.mark.asyncio
async def test_exec_reports_the_missing_vm_instead_of_running_on_the_host() -> None:
    gateway = SandboxGateway(
        SandboxConfig(enabled=True, backend="down", workspace="/workspace", desktop_enabled=False),
        backend=DownBackend(),
    )
    set_sandbox_gateway(gateway)
    try:
        result = await ExecTool().execute(command="echo should-not-run")
    finally:
        set_sandbox_gateway(None)
    assert "Error" in result
    assert "no virtualization" in result
