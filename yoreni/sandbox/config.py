"""Configuration for the persistent execution VM."""

from __future__ import annotations

from pydantic import Field

from yoreni.config_base import Base


class SandboxConfig(Base):
    """VM lifecycle and desktop settings.

    ``workspace`` is a path *inside* the VM. The host filesystem is never a
    workspace root: a host path reaches the VM only through the explicit
    import/export methods on the gateway.
    """

    enabled: bool = True
    backend: str = "boxlite"
    box_name: str = "yoreni"
    image: str = "lscr.io/linuxserver/webtop:ubuntu-xfce"
    cpus: int = Field(default=2, ge=1, le=32)
    memory_mib: int = Field(default=4096, ge=512)
    disk_size_gb: int = Field(default=20, ge=2)
    workspace: str = "/workspace"
    media_dir: str = "/workspace/.yoreni/media"
    downloads_dir: str = "/workspace/downloads"
    browser_profile_dir: str = "/workspace/.yoreni/chromium"
    cdp_guest_port: int = 9223
    display: str = ":1"
    display_width: int = Field(default=1440, ge=640)
    display_height: int = Field(default=900, ge=480)
    desktop_enabled: bool = True
    browser_enabled: bool = True
    start_timeout_s: int = Field(default=300, ge=30)
    provision_timeout_s: int = Field(default=900, ge=60)
    exec_timeout_s: int = Field(default=3600, ge=1)
    exec_max_output_chars: int = Field(default=200_000, ge=1000)
    takeover_grace_s: float = Field(default=10.0, ge=0.0)
    desktop_token_ttl_s: int = Field(default=60, ge=5, le=300)
