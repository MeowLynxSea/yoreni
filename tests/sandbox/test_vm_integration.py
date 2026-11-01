"""Real microVM integration checks.

Skipped unless YORENI_SANDBOX_INTEGRATION=1 and hardware virtualization is
available. These tests intentionally create a real BoxLite VM; they are not
mocks and must not be reported as passing when skipped.
"""

from __future__ import annotations

import os

import pytest

from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.gateway import SandboxGateway

pytestmark = pytest.mark.skipif(
    os.environ.get("YORENI_SANDBOX_INTEGRATION") != "1",
    reason="set YORENI_SANDBOX_INTEGRATION=1 to run a real microVM",
)


def _config(name: str) -> SandboxConfig:
    return SandboxConfig(
        enabled=True,
        backend="boxlite",
        box_name=os.environ.get("YORENI_SANDBOX_BOX", name),
        image="lscr.io/linuxserver/webtop:ubuntu-lxqt",
        workspace="/workspace",
        media_dir="/workspace/.yoreni/media",
        desktop_enabled=True,
        browser_enabled=True,
        start_timeout_s=600,
        provision_timeout_s=1200,
    )


@pytest.mark.asyncio
async def test_agent_execution_runs_in_a_real_vm_and_persists() -> None:
    gateway = SandboxGateway(_config("yoreni-integration"))
    try:
        await gateway.start()
        result = await gateway.exec("uname -s; id -u; echo $HOME", cwd="/workspace")
        assert result.exit_code == 0
        assert "Linux" in result.stdout

        await gateway.write_file("/workspace/integration.txt", b"persisted")
        assert await gateway.read_file("/workspace/integration.txt") == b"persisted"

        # The host user's home directory is not the VM's filesystem.
        outside = await gateway.exec(
            "test -e /Users && echo host-home-visible || echo isolated",
            cwd="/workspace",
        )
        assert "isolated" in outside.stdout
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_desktop_and_browser_services_become_ready() -> None:
    gateway = SandboxGateway(_config("yoreni-integration-desktop"))
    try:
        await gateway.start()
        health = await gateway.health()
        assert health.desktop_ready
        assert health.browser_ready
        screenshot = await gateway.desktop_screenshot()
        assert screenshot.startswith(b"\x89PNG")
    finally:
        await gateway.close()
