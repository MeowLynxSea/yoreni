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
    # `boxlite` runs the VM on the host; `podroid` drives an Alpine VM that a
    # separate Android app owns.
    backend: str = "boxlite"
    box_name: str = "yoreni"
    # Alpine + XFCE is the same guest the Android backend runs, so one
    # provisioning path covers both hosts. The image ships Chromium.
    # LinuxServer has no alpine-xfce tag: latest is the XFCE Alpine build.
    image: str = "lscr.io/linuxserver/webtop:latest"
    # Runtime home for images, box disks and the runtime database. Empty means
    # `<instance data dir>/sandbox`, so one backup or deletion covers all
    # Yoreni state.
    home_dir: str = ""
    cpus: int = Field(default=2, ge=1, le=32)
    memory_mib: int = Field(default=4096, ge=512)
    disk_size_gb: int = Field(default=20, ge=2)
    workspace: str = "/workspace"
    media_dir: str = "/workspace/.yoreni/media"
    downloads_dir: str = "/workspace/downloads"
    browser_profile_dir: str = "/workspace/.yoreni/chromium"
    display: str = ":1"
    display_width: int = Field(default=1440, ge=640)
    display_height: int = Field(default=900, ge=480)
    desktop_enabled: bool = True
    browser_enabled: bool = True
    # A cold first start rebuilds the VM disk and boots it after the image
    # transfer; five minutes did not cover that and failed setups that were
    # about to succeed.
    start_timeout_s: int = Field(default=900, ge=30)
    provision_timeout_s: int = Field(default=900, ge=60)
    exec_timeout_s: int = Field(default=3600, ge=1)
    exec_max_output_chars: int = Field(default=200_000, ge=1000)
    takeover_grace_s: float = Field(default=10.0, ge=0.0)
    desktop_token_ttl_s: int = Field(default=60, ge=5, le=300)
    # -- Podroid backend -----------------------------------------------------
    # Podroid exposes its VM over SSH on loopback, so the gateway never needs a
    # published port. The defaults match the app's own SSH settings; change the
    # password to a key before relying on it.
    podroid_host: str = "127.0.0.1"
    podroid_port: int = Field(default=9922, ge=1, le=65535)
    podroid_user: str = "root"
    podroid_password: str = "podroid"
    podroid_key_path: str = ""
    # Podroid's X server is :0; the webtop images put it on :1.
    podroid_display: str = ":0"
    # The app's own adb path; the Android host injects a controller instead.
    podroid_adb: str = "adb"
    # A loopback endpoint the host serves (start/stop). Takes precedence over
    # adb, which an Android app cannot use.
    podroid_control_url: str = ""
    podroid_start_timeout_s: int = Field(default=180, ge=30)
