"""First-boot provisioning for the Yoreni desktop VM.

The base image is a maintained LinuxServer webtop image (Alpine, XFCE). It
already owns a display, a window manager and an X server; this module adds the
pieces Yoreni needs and keeps them inside the VM:

* x11vnc for a raw RFB surface that the gateway can proxy and gate
* websockify so the WebUI speaks RFB over WebSocket through the gateway
* the Alpine packages the desktop, the terminal and MCP servers need

The browser is the image's own Chromium; 'browser_assets' only places the
BrowserSkill CLI and its extension next to it.

Every command runs inside the VM. Nothing here touches the host filesystem.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Mapping

from loguru import logger

from yoreni.sandbox.backend import SandboxBackend
from yoreni.sandbox.browser_assets import BSK_HOME, VM_EXTENSION_DIR
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError

# What the desktop, the shell and the agent's file tools need from apk.
# Chromium is listed so an image without a browser still gains one.
_DESKTOP_PACKAGES = (
    "x11vnc novnc websockify xdotool xclip scrot ripgrep python3 "
    "curl ca-certificates chromium xsetroot xfconf iproute2 ttf-dejavu font-wqy-zenhei"
)
PROVISION_LOG = "/workspace/.yoreni/logs/provision.log"

_PROVISION = r"""
set -eu
if ! command -v apk >/dev/null 2>&1; then
  echo 'This desktop image is not Alpine. Rebuild the VM so it uses the default Alpine image.' >&2
  exit 12
fi
mkdir -p /workspace/.yoreni/media /workspace/.yoreni/logs /workspace/downloads
mkdir -p /workspace/projects
chmod 0777 /workspace /workspace/.yoreni /workspace/.yoreni/media /workspace/downloads /workspace/projects
missing=""
for package in {packages}; do
  apk info -e "$package" >/dev/null 2>&1 || missing=1
done
if [ -n "$missing" ]; then
  : > {log}
  # The whole list, not only the missing packages: apk resolves other
  # packages against it, and asking for less has dead-ended before. It
  # leaves what is already current alone.
  if ! apk add --no-cache {packages} >>{log} 2>&1; then
    head -n 15 {log} >&2
    echo '...' >&2
    tail -n 40 {log} >&2
    exit 1
  fi
fi
# Node only serves npx-based MCP servers. The desktop must come up without
# it, so a dead end here is recorded instead of failing the whole provision.
if ! command -v npx >/dev/null 2>&1; then
  apk add --no-cache nodejs npm >>{log} 2>&1 ||
    echo 'Node.js is unavailable; npx-based MCP servers will not start' >>{log} 2>&1
fi
"""

_START_DESKTOP = r"""
set -eu
LOGS=/workspace/.yoreni/logs
NOVNC=/usr/share/novnc
# Stop whatever holds our two ports, then the process groups this script
# recorded. A command-line match is not usable here: this script runs as one
# shell string, so its own text would match. Neither port may be one the guest
# already serves — on Podroid that is its own VNC server.
for port in {vnc_port} {ws_port}; do
  pid=$(ss -lntp 2>/dev/null | sed -n "s/.*:$port .*pid=\([0-9]*\).*/\1/p" | head -n 1)
  if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
done
for name in x11vnc websockify; do
  pidfile=$LOGS/$name.pid
  if [ ! -f "$pidfile" ]; then continue; fi
  kill -- "-$(cat "$pidfile")" 2>/dev/null || true
  rm -f "$pidfile"
done
sleep 1
setsid x11vnc -display {display} -rfbport {vnc_port} -forever -shared -viewonly \
  -nopw -localhost -quiet -noxdamage -noshm >"$LOGS/x11vnc.log" 2>&1 </dev/null &
echo $! >"$LOGS/x11vnc.pid"
setsid websockify --web="$NOVNC" {ws_port} 127.0.0.1:{vnc_port} \
  >"$LOGS/websockify.log" 2>&1 </dev/null &
echo $! >"$LOGS/websockify.pid"
sleep 1
"""

# Podroid already runs the guest's X server and a VNC server for it, and it
# holds the credentials for that X display. Yoreni therefore relays that server
# instead of mirroring it, which would need an auth cookie it cannot read.
_START_DESKTOP_PODROID = r"""
set -eu
LOGS=/workspace/.yoreni/logs
NOVNC=/usr/share/novnc
pid=$(ss -lntp 2>/dev/null | sed -n "s/.*:{ws_port} .*pid=\([0-9]*\).*/\1/p" | head -n 1)
if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
if [ -f "$LOGS/websockify.pid" ]; then
  kill -- "-$(cat "$LOGS/websockify.pid")" 2>/dev/null || true
  rm -f "$LOGS/websockify.pid"
fi
sleep 1
setsid websockify --web="$NOVNC" {ws_port} 127.0.0.1:{vnc_port} \
  >"$LOGS/websockify.log" 2>&1 </dev/null &
echo $! >"$LOGS/websockify.pid"
sleep 1
"""

_START_BROWSER = r"""
set -eu
export DISPLAY={display}
LOGS=/workspace/.yoreni/logs
# The daemon's own files are private to the user that creates them, so it runs
# as the desktop user: a terminal there stays usable, and root can still read
# them. Without this the VM's human and the agent see different daemons.
mkdir -p {bsk_home}
BSK_USER=root
if id abc >/dev/null 2>&1; then BSK_USER=abc; fi
chown -R "$BSK_USER" {bsk_home}
chmod 0777 {bsk_home}
printf 'export BSK_HOME=%s\nexport BSK_AUTO_START=0\n' {bsk_home} \
  >/etc/profile.d/yoreni-bsk.sh
as_bsk_user() {{
  su -s /bin/sh "$BSK_USER" -c "BSK_HOME={bsk_home} BSK_AUTO_START=0 bsk $*"
}}
if ! as_bsk_user status --json >/dev/null 2>&1; then
  # A daemon left by another user holds the port and the lock, so a fresh one
  # cannot become ready. Stop it, then clear what the graceful stop may have
  # left behind before starting the replacement.
  BSK_HOME={bsk_home} BSK_AUTO_START=0 bsk daemon stop >/dev/null 2>&1 || true
  pkill -x bsk >/dev/null 2>&1 || true
  sleep 1
  rm -f {bsk_home}/daemon.json {bsk_home}/run/daemon.sock
  as_bsk_user daemon start >>"$LOGS/bsk-daemon.log" 2>&1 || true
fi
pkill -x chromium >/dev/null 2>&1 || true
sleep 1
# The image's Chromium is the only browser: there is no second download.
BROWSER=$(command -v chromium-browser 2>/dev/null || command -v chromium 2>/dev/null || true)
if [ -z "$BROWSER" ]; then
  echo "No Chromium is installed in the VM" >&2
  exit 11
fi
mkdir -p {profile}
# A profile left behind by a killed instance keeps a stale singleton lock.
rm -f {profile}/SingletonLock {profile}/SingletonSocket {profile}/SingletonCookie
nohup "$BROWSER" --user-data-dir={profile} --no-sandbox --disable-gpu \
  --disable-dev-shm-usage --no-first-run --no-default-browser-check \
  --load-extension={extension} --disable-extensions-except={extension} \
  --start-maximized about:blank >"$LOGS/chromium.log" 2>&1 &
"""

_PORT_OPEN = (
    'python3 -c "import socket,sys;socket.create_connection((\'127.0.0.1\',{}),2).close()"'
)
VNC_PORT = 5900
WS_PORT = 6080
_DESKTOP_READY = (
    "xdotool getdisplaygeometry >/dev/null 2>&1 && "
    "pgrep -f '[x]11vnc .*-rfbport {vnc_port}' >/dev/null && "
    + _PORT_OPEN.format("{vnc_port}")
    + " && pgrep -f '[w]ebsockify .*{ws_port}' >/dev/null && "
    + _PORT_OPEN.format("{ws_port}")
)
# BrowserSkill's extension connects to the daemon on loopback and registers
# itself as a browser; both pieces must be up before the agent can act.
_BROWSERS = (
    "import json, os, subprocess\n"
    "def browsers():\n"
    "    env = dict(os.environ, BSK_AUTO_START='0')\n"
    "    try:\n"
    "        r = subprocess.run(['bsk', 'status', '--json'], capture_output=True,\n"
    "                           text=True, timeout=8, env=env)\n"
    "        return len(json.loads(r.stdout).get('browsers') or [])\n"
    "    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):\n"
    "        return 0\n"
)
_BROWSER_READY = (
    "(pgrep -x chromium >/dev/null || pgrep -x chromium-browser >/dev/null) && "
    'python3 -c "' + _BROWSERS + 'print(int(browsers() > 0))" | grep -qx 1'
)
# One round trip for both services: spawning a shell per probe dominates the
# latency the WebUI sees while it polls status.
# A guest that owns the VNC server is ready when that server and the WebSocket
# relay both answer; there is no mirror to ask about.
_DESKTOP_RELAY_READY = (
    _PORT_OPEN.format("{vnc_port}")
    + " && pgrep -f '[w]ebsockify .*{ws_port}' >/dev/null && "
    + _PORT_OPEN.format("{ws_port}")
)
_READINESS = (
    'python3 -c "import json, os, socket, subprocess\n'
    "def ok(port):\n"
    "    try:\n"
    "        socket.create_connection(('127.0.0.1', port), 2).close()\n"
    "        return True\n"
    "    except OSError:\n"
    "        return False\n"
    + _BROWSERS
    + 'print(int(ok({vnc_port}) and ok({ws_port})), '
    'int(ok(52800) and browsers() > 0))"'
)



class DesktopServices:
    """Provision and observe the VNC, WebSocket and BrowserSkill services."""

    def __init__(self, backend: SandboxBackend, config: SandboxConfig) -> None:
        self._backend = backend
        self._config = config
        self._desktop_ready = False
        self._browser_ready = False

    @property
    def _relays_guest_vnc(self) -> bool:
        """True when the guest already serves VNC for the desktop it owns.

        Podroid runs both the X server and a VNC server for it and keeps that
        display's credentials to itself, so Yoreni relays it rather than
        mirroring it. Replacing it would take the desktop down with it.
        """
        return self._backend.name == "podroid"

    @property
    def _vnc_port(self) -> int:
        """The VNC port websockify forwards: ours, or the guest's own."""
        return VNC_PORT

    @property
    def _ports(self) -> dict[str, int]:
        return {"vnc_port": self._vnc_port, "ws_port": WS_PORT, "display": shlex.quote(self._config.display)}

    @property
    def desktop_ready(self) -> bool:
        return self._desktop_ready

    @property
    def browser_ready(self) -> bool:
        return self._browser_ready

    def reset(self) -> None:
        """Forget readiness observed for a VM that no longer exists."""
        self._desktop_ready = False
        self._browser_ready = False

    async def provision(self) -> None:
        script = _PROVISION.format(
            packages=_DESKTOP_PACKAGES,
            log=shlex.quote(PROVISION_LOG),
        )
        result = await self._backend.exec(
            script,
            cwd="/",
            timeout_s=self._config.provision_timeout_s,
            user="root",
        )
        if result.exit_code == 12:
            raise SandboxUnavailableError(
                "The VM still runs the previous desktop image and cannot be provisioned. "
                "Rebuild the virtual machine to move it to the Alpine image."
            )
        if result.exit_code != 0:
            raise SandboxUnavailableError(
                "VM provisioning failed. The desktop image needs network access to "
                f"install its system packages. Full output in {PROVISION_LOG}: "
                f"{result.stderr.strip()[-800:]}"
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
        template = _START_DESKTOP_PODROID if self._relays_guest_vnc else _START_DESKTOP
        script = template.format(**self._ports)
        result = await self._backend.exec(script, cwd="/", timeout_s=60, user="root")
        if result.exit_code != 0:
            raise SandboxUnavailableError(
                "The VM desktop could not start x11vnc/noVNC: "
                + (result.stderr.strip() or result.stdout.strip())[-800:]
                + await self._log_tail("/workspace/.yoreni/logs/x11vnc.log")
            )
        # xdotool asks the X server for the screen size, so the probe needs the
        # same display x11vnc mirrors; without it every start reports a desktop
        # that is running as not ready.
        readiness = (
            _DESKTOP_RELAY_READY if self._relays_guest_vnc else _DESKTOP_READY
        )
        self._desktop_ready = await self._wait(
            readiness.format(**self._ports),
            timeout_s=30,
            user="root",
            env={"DISPLAY": self._config.display},
        )
        if not self._desktop_ready:
            raise SandboxUnavailableError(
                "The VM desktop did not become ready. "
                + await self._log_tail("/workspace/.yoreni/logs/x11vnc.log")
                + await self._log_tail("/workspace/.yoreni/logs/websockify.log")
            )

    async def _start_browser(self) -> None:
        script = _START_BROWSER.format(
            display=shlex.quote(self._config.display),
            profile=shlex.quote(self._config.browser_profile_dir),
            extension=shlex.quote(VM_EXTENSION_DIR),
            bsk_home=shlex.quote(BSK_HOME),
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
                "The VM browser did not connect to BrowserSkill.\n"
                + await self._process_snapshot("chromium|bsk")
                + await self._log_tail("/workspace/.yoreni/logs/chromium.log")
                + await self._log_tail("/workspace/.yoreni/logs/bsk-daemon.log")
            )

    async def _process_snapshot(self, pattern: str) -> str:
        result = await self._backend.exec(
            f"ss -lnt 2>/dev/null | grep -E ':(52800|{self._vnc_port}|{WS_PORT})' "
            "|| echo 'no service listeners'; "
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

    async def _wait(
        self,
        command: str,
        *,
        timeout_s: float,
        user: str | None,
        env: Mapping[str, str] | None = None,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            result = await self._backend.exec(
                command, cwd="/", timeout_s=10, user=user, env=env,
            )
            if result.exit_code == 0:
                return True
            await asyncio.sleep(1)
        return False

    async def set_view_only(self, view_only: bool) -> None:
        if not self._config.desktop_enabled:
            return
        if self._relays_guest_vnc:
            # Podroid's own VNC server has no view-only switch; input authority
            # stays with the UI and the control state machine.
            logger.debug("Skipping x11vnc {viewonly} on the guest's VNC server")
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
        """Probe both services in a single VM round trip."""
        if not (self._config.desktop_enabled or self._config.browser_enabled):
            return False, False
        result = await self._backend.exec(
            _READINESS.format(**self._ports),
            cwd="/",
            timeout_s=10,
            user="root",
        )
        parts = result.stdout.split()
        if len(parts) != 2:
            self._desktop_ready = self._browser_ready = False
        else:
            self._desktop_ready = self._config.desktop_enabled and parts[0] == "1"
            self._browser_ready = self._config.browser_enabled and parts[1] == "1"
        return self._desktop_ready, self._browser_ready
