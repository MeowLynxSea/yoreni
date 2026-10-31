"""First-boot provisioning for the Yoreni desktop VM.

The base image is a maintained LinuxServer webtop image: it already owns a
display, a window manager and a Chromium-capable X server. This module adds
the three pieces Yoreni needs and keeps them inside the VM:

* x11vnc for a raw RFB surface that the gateway can proxy and gate
* websockify so the WebUI speaks RFB over WebSocket through the gateway
* Playwright's Chromium build, driven over CDP

Every command runs inside the VM. Nothing here touches the host filesystem.
"""

from __future__ import annotations

import asyncio
import shlex

from loguru import logger

from yoreni.sandbox.backend import SandboxBackend
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError

PROVISION_MARKER = "/workspace/.yoreni/provisioned-v2"
_BROWSER_PATH = "/workspace/.yoreni/ms-playwright"

_PROVISION = r"""
set -eu
mkdir -p /workspace/.yoreni/media /workspace/.yoreni/logs /workspace/downloads
mkdir -p /workspace/projects
chmod 0777 /workspace /workspace/.yoreni /workspace/.yoreni/media /workspace/downloads /workspace/projects
if [ ! -f {marker} ]; then
  export DEBIAN_FRONTEND=noninteractive
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y --no-install-recommends x11vnc novnc websockify xdotool xclip scrot ripgrep socat python3 python3-pip curl ca-certificates
  fi
  export PLAYWRIGHT_BROWSERS_PATH={browser_path}
  python3 -m pip install --break-system-packages --no-cache-dir playwright >/dev/null 2>&1 || \
    python3 -m pip install --no-cache-dir playwright
  python3 -m playwright install --with-deps chromium
  touch {marker}
fi
"""

_START_DESKTOP = r"""
set -eu
LOGS=/workspace/.yoreni/logs
pkill -x x11vnc 2>/dev/null || true
pkill -x websockify 2>/dev/null || true
nohup x11vnc -display {display} -rfbport 5900 -forever -shared -viewonly \
  -nopw -localhost -quiet -noxdamage -noshm >"$LOGS/x11vnc.log" 2>&1 &
nohup websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 \
  >"$LOGS/websockify.log" 2>&1 &
sleep 1
"""

_START_BROWSER = r"""
set -eu
LOGS=/workspace/.yoreni/logs
pkill -x chrome 2>/dev/null || true
sleep 1
CHROME=$(ls -d {browser_path}/chromium-*/chrome-linux*/chrome 2>/dev/null | head -n 1)
if [ -z "$CHROME" ]; then
  echo "Chromium was not installed" >&2
  exit 11
fi
mkdir -p {profile}
# A profile left behind by a killed instance keeps a stale singleton lock.
rm -f {profile}/SingletonLock {profile}/SingletonSocket {profile}/SingletonCookie
nohup "$CHROME" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
  --user-data-dir={profile} --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --no-first-run --no-default-browser-check --start-maximized about:blank \
  >"$LOGS/chromium.log" 2>&1 &
pkill -x socat 2>/dev/null || true
nohup socat TCP-LISTEN:{cdp_port},bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:9222 \
  >"$LOGS/cdp-relay.log" 2>&1 &
"""

_PORT_OPEN = (
    "python3 -c \"import socket,sys;socket.create_connection(('127.0.0.1',{}),2).close()\""
)
_DESKTOP_READY = (
    "xdotool getdisplaygeometry >/dev/null 2>&1 && "
    "pgrep -f 'x11vnc .*-rfbport 5900' >/dev/null && "
    + _PORT_OPEN.format(5900)
    + " && pgrep -f 'websockify .*6080' >/dev/null && "
    + _PORT_OPEN.format(6080)
)
_BROWSER_READY = (
    "python3 -c \"import urllib.request;"
    "urllib.request.urlopen('http://127.0.0.1:9222/json/version', timeout=2)\" "
    ">/dev/null 2>&1 && pgrep -x socat >/dev/null"
)


_STYLE_DESKTOP = r"""
export DISPLAY={display}
# The stock wallpaper is a photo of someone else's desktop. A flat Yoreni
# surface reads as one continuous app instead of a remote computer.
for prop in $(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep '/last-image$'); do
  xfconf-query -c xfce4-desktop -p "${{prop%/last-image}}/image-style" -s 0 >/dev/null 2>&1 || true
done
for prop in $(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep '/color-style$'); do
  xfconf-query -c xfce4-desktop -p "$prop" -s 0 >/dev/null 2>&1 || true
done
for prop in $(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep '/rgba1$'); do
  xfconf-query -c xfce4-desktop -p "$prop" -t double -s 0.043 -s 0.051 -s 0.063 -s 1.0 \
    >/dev/null 2>&1 || true
done
"""



class DesktopServices:
    """Provision and observe the VNC, WebSocket and CDP services."""

    def __init__(self, backend: SandboxBackend, config: SandboxConfig) -> None:
        self._backend = backend
        self._config = config
        self._desktop_ready = False
        self._browser_ready = False

    @property
    def desktop_ready(self) -> bool:
        return self._desktop_ready

    @property
    def browser_ready(self) -> bool:
        return self._browser_ready

    async def provision(self) -> None:
        script = _PROVISION.format(
            marker=shlex.quote(PROVISION_MARKER),
            browser_path=shlex.quote(_BROWSER_PATH),
        )
        result = await self._backend.exec(
            script,
            cwd="/",
            timeout_s=self._config.provision_timeout_s,
            user="root",
        )
        if result.exit_code != 0:
            raise SandboxUnavailableError(
                "VM provisioning failed. The desktop image needs network access to "
                f"install x11vnc, noVNC and Chromium: {result.stderr.strip()[-800:]}"
            )

    async def start(self) -> None:
        if self._config.desktop_enabled:
            await self._start_desktop()
        if self._config.browser_enabled:
            await self._start_browser()

    async def start_browser(self) -> None:
        """Start Chromium alone, leaving a healthy desktop untouched."""
        await self._start_browser()

    async def _start_desktop(self) -> None:
        script = _START_DESKTOP.format(display=shlex.quote(self._config.display))
        result = await self._backend.exec(script, cwd="/", timeout_s=60, user="root")
        if result.exit_code != 0:
            raise SandboxUnavailableError(
                "The VM desktop could not start x11vnc/noVNC: "
                + (result.stderr.strip() or result.stdout.strip())[-800:]
                + await self._log_tail("/workspace/.yoreni/logs/x11vnc.log")
            )
        await self._apply_desktop_style()
        self._desktop_ready = await self._wait(_DESKTOP_READY, timeout_s=30, user="root")
        if not self._desktop_ready:
            raise SandboxUnavailableError(
                "The VM desktop did not become ready. "
                + await self._log_tail("/workspace/.yoreni/logs/x11vnc.log")
                + await self._log_tail("/workspace/.yoreni/logs/websockify.log")
            )

    async def _apply_desktop_style(self) -> None:
        """Give the VM desktop Yoreni's flat surface instead of stock art."""
        script = _STYLE_DESKTOP.format(display=shlex.quote(self._config.display))
        try:
            await self._backend.exec(
                script,
                cwd="/",
                env={"DISPLAY": self._config.display, "HOME": "/root"},
                timeout_s=20,
                user="root",
            )
        except Exception as exc:
            logger.debug("Could not restyle the VM desktop: {}", exc)

    async def _start_browser(self) -> None:
        script = _START_BROWSER.format(
            browser_path=shlex.quote(_BROWSER_PATH),
            profile=shlex.quote(self._config.browser_profile_dir),
            cdp_port=self._config.cdp_guest_port,
        )
        # A cold Chromium on a software-rendered VM needs well over half a
        # minute before it serves /json/version.
        result = await self._backend.exec(
            script,
            cwd="/",
            env={"DISPLAY": self._config.display},
            timeout_s=120,
            user="root",
        )
        if result.exit_code != 0:
            raise SandboxUnavailableError(
                "The VM browser could not start: "
                + (result.stderr.strip() or result.stdout.strip())[-800:]
                + await self._log_tail("/workspace/.yoreni/logs/chromium.log")
            )
        self._browser_ready = await self._wait(_BROWSER_READY, timeout_s=90, user="root")
        if not self._browser_ready:
            raise SandboxUnavailableError(
                "Chromium did not open its CDP endpoint.\n"
                + await self._process_snapshot("chromium|chrome|socat")
                + await self._log_tail("/workspace/.yoreni/logs/chromium.log")
            )

    async def _process_snapshot(self, pattern: str) -> str:
        result = await self._backend.exec(
            "ss -lnt 2>/dev/null | grep -E ':(9222|9223)' || echo 'no CDP listeners'; "
            f"ps -eo pid,stat,args | grep -E '[{pattern[0]}]{pattern[1:]}' | head -n 5 || true",
            cwd="/",
            timeout_s=10,
            user="root",
        )
        text = (result.stdout or result.stderr).strip()
        return f"{text[-1200:]}\n" if text else ""

    async def _log_tail(self, path: str) -> str:
        """Return the last lines of a VM log so failures are diagnosable."""
        result = await self._backend.exec(
            f"tail -n 20 {shlex.quote(path)} 2>/dev/null",
            cwd="/",
            timeout_s=10,
            user="root",
        )
        tail = (result.stdout or result.stderr).strip()
        return f"\n{tail[-1500:]}" if tail else f"\n{path} is empty"

    async def log_tail(self, name: str) -> str:
        """Return the tail of one desktop service log by file name."""
        return await self._log_tail(f"/workspace/.yoreni/logs/{name}")

    async def diagnostics(self) -> str:
        """Summarize the desktop services for a failed viewer connection."""
        script = (
            "ps -eo pid,args | grep -E '[x]11vnc|[w]ebsockify' || true; "
            "ss -lnt 2>/dev/null | grep -E ':(5900|6080)' || echo 'no listeners'; "
            "echo '--- x11vnc'; tail -n 6 /workspace/.yoreni/logs/x11vnc.log 2>/dev/null; "
            "echo '--- websockify'; tail -n 6 /workspace/.yoreni/logs/websockify.log 2>/dev/null"
        )
        result = await self._backend.exec(script, cwd="/", timeout_s=10, user="root")
        return (result.stdout or result.stderr).strip()[-1500:]

    async def _wait(self, command: str, *, timeout_s: float, user: str | None) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            result = await self._backend.exec(command, cwd="/", timeout_s=10, user=user)
            if result.exit_code == 0:
                return True
            await asyncio.sleep(1)
        return False

    async def set_view_only(self, view_only: bool) -> None:
        if not self._config.desktop_enabled:
            return
        remote = "viewonly" if view_only else "noviewonly"
        try:
            result = await asyncio.wait_for(
                self._backend.exec(
                    ("x11vnc", "-display", self._config.display, "-R", remote),
                    cwd="/",
                    env={"DISPLAY": self._config.display, "HOME": "/root"},
                    timeout_s=10,
                    user="root",
                ),
                timeout=20,
            )
        except Exception as exc:
            logger.warning("Could not switch x11vnc {}: {}", remote, exc)
            return
        if result.exit_code != 0:
            logger.warning(
                "Could not switch x11vnc {}: {}",
                remote,
                (result.stderr or result.stdout).strip()[:200],
            )

    async def health(self) -> tuple[bool, bool]:
        if self._config.desktop_enabled:
            desktop = await self._backend.exec(
                _DESKTOP_READY,
                cwd="/",
                timeout_s=10,
                user="root",
            )
            self._desktop_ready = desktop.exit_code == 0
        if self._config.browser_enabled:
            browser = await self._backend.exec(
                _BROWSER_READY,
                cwd="/",
                timeout_s=10,
                user="root",
            )
            self._browser_ready = browser.exit_code == 0
        return self._desktop_ready, self._browser_ready
