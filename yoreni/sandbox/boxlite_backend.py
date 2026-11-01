# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

"""BoxLite-backed microVM execution.

BoxLite gives the sandbox a separate kernel, a persistent QCOW2 disk and
local port forwarding without a daemon. This module owns only that mechanism;
workspace policy, control ownership and provisioning live above it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from loguru import logger

from yoreni.sandbox.backend import ExecutionHandle, split_command
from yoreni.sandbox.browser_assets import BSK_HOME
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.models import ExecResult, FileInfo, SandboxHealth, SandboxStatus

_DIRECT_MAX_BYTES = 32 * 1024 * 1024
_STAT_SCRIPT = """
import json, os, stat, sys
p = sys.argv[1]
try:
    st = os.lstat(p)
except FileNotFoundError:
    sys.exit(1)
print(json.dumps({
    "path": p,
    "is_dir": stat.S_ISDIR(st.st_mode),
    "is_file": stat.S_ISREG(st.st_mode),
    "is_symlink": stat.S_ISLNK(st.st_mode),
    "size": st.st_size,
    "mode": stat.S_IMODE(st.st_mode),
    "mtime_s": st.st_mtime,
}))
"""
_LIST_SCRIPT = """
import json, os, stat, sys
root = sys.argv[1]
try:
    names = sorted(os.listdir(root))
except FileNotFoundError:
    sys.exit(1)
for name in names:
    p = os.path.join(root, name)
    try:
        st = os.lstat(p)
    except OSError:
        continue
    print(json.dumps({
        "path": p,
        "is_dir": stat.S_ISDIR(st.st_mode),
        "is_file": stat.S_ISREG(st.st_mode),
        "is_symlink": stat.S_ISLNK(st.st_mode),
        "size": st.st_size,
        "mode": stat.S_IMODE(st.st_mode),
        "mtime_s": st.st_mtime,
    }))
"""


class BoxLiteBackend:
    name = "boxlite"

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._box: Any = None
        self._runtime: Any = None
        self._staging = Path(tempfile.mkdtemp(prefix="yoreni-sandbox-"))
        self._lock = asyncio.Lock()
        self._tunnels: dict[int, Any] = {}
        self._started_at_ms: int | None = None

    # -- lifecycle -----------------------------------------------------------

    def data_dir(self) -> Path:
        """Return the runtime home that holds images, boxes and the database."""
        configured = self._config.home_dir.strip()
        if configured:
            return Path(configured).expanduser()
        from yoreni.config.paths import get_data_dir

        return get_data_dir() / "sandbox"

    async def _ensure_box(self) -> Any:
        if self._box is not None:
            return self._box
        try:
            import boxlite
        except ImportError as exc:
            raise SandboxUnavailableError(
                "BoxLite is not installed. Install the 'sandbox' extra and enable "
                "hardware virtualization (KVM on Linux, Apple Silicon on macOS)."
            ) from exc
        try:
            runtime = boxlite.Boxlite(boxlite.Options(home_dir=str(self.data_dir())))
            options = boxlite.BoxOptions(
                image=self._config.image,
                cpus=self._config.cpus,
                memory_mib=self._config.memory_mib,
                disk_size_gb=self._config.disk_size_gb,
                auto_delete=0,
                auto_remove=False,
                detach=True,
                # The webtop image boots a Wayland session by default, which has
                # no X display for x11vnc to capture; these turn it back into the
                # X11 session the desktop services are built around, at the
                # configured resolution instead of the image's 1024x768.
                env=[
                    ("DISPLAY", self._config.display),
                    ("PIXELFLUX_WAYLAND", "false"),
                    ("SELKIES_MANUAL_WIDTH", str(self._config.display_width)),
                    ("SELKIES_MANUAL_HEIGHT", str(self._config.display_height)),
                ],
            )
            box, _created = await runtime.get_or_create(
                options,
                name=self._config.box_name,
            )
        except Exception as exc:
            raise SandboxUnavailableError(f"could not create the Yoreni VM: {exc}") from exc
        self._runtime = runtime
        self._box = box
        return box

    async def prepare(self) -> None:
        """Pull the image and create the VM without booting it."""
        async with self._lock:
            await self._ensure_box()

    async def start(self) -> None:
        async with self._lock:
            box = await self._ensure_box()
            try:
                info = await box.info()
                if getattr(info.state, "status", "") != "running":
                    await box.start()
                self._started_at_ms = int(time.time() * 1000)
            except Exception as exc:
                raise SandboxUnavailableError(f"could not start the Yoreni VM: {exc}") from exc

    async def stop(self) -> None:
        async with self._lock:
            if self._box is None:
                return
            await self._close_tunnels()
            try:
                await self._box.stop()
            except Exception as exc:
                raise SandboxUnavailableError(f"could not stop the Yoreni VM: {exc}") from exc
            self._started_at_ms = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def status(self) -> SandboxStatus:
        if self._box is None:
            state = "created"
            box_id = None
        else:
            try:
                info = await self._box.info()
                raw = str(getattr(info.state, "status", "unknown"))
                box_id = str(getattr(info, "id", "") or "") or None
            except Exception:
                raw = "unknown"
                box_id = None
            state = (
                raw if raw in {"created", "starting", "running", "stopped", "failed"}
                else "unknown"
            )
        return SandboxStatus(
            backend=self.name,
            box_id=box_id,
            name=self._config.box_name,
            state=cast(Any, state),
            workspace=self._config.workspace,
            health=SandboxHealth(running=state == "running", desktop_ready=False, browser_ready=False),
            started_at_ms=self._started_at_ms,
            detail="" if state == "running" else f"VM state: {state}",
        )

    async def is_running(self) -> bool:
        return (await self.status()).state == "running"

    # -- execution -----------------------------------------------------------

    def _env(self, extra: Mapping[str, str] | None) -> list[tuple[str, str]]:
        env = {
            "HOME": self._config.workspace,
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": "/tmp",
            "TERM": "xterm-256color",
            # The BrowserSkill daemon is started by provisioning, not by agent
            # commands; every shell sees the same state directory.
            "BSK_HOME": BSK_HOME,
            "BSK_AUTO_START": "0",
        }
        for key, value in (extra or {}).items():
            if not key or "=" in key or "\x00" in key or "\x00" in str(value):
                continue
            env[key] = str(value)
        return list(env.items())

    async def exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
    ) -> ExecResult:
        started = time.monotonic()
        binary, args = split_command(command)
        box = await self._ensure_box()
        try:
            execution = await box.exec(
                binary,
                args,
                self._env(env),
                user=user,
                timeout_secs=timeout_s,
                cwd=cwd,
            )
            stdout_task = asyncio.create_task(_collect(execution.stdout()))
            stderr_task = asyncio.create_task(_collect(execution.stderr()))
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            result = await execution.wait()
        except Exception as exc:
            raise SandboxUnavailableError(f"VM execution failed: {exc}") from exc
        return ExecResult(
            command=binary if isinstance(command, str) else " ".join(map(str, command)),
            exit_code=int(getattr(result, "exit_code", -1)),
            stdout=stdout,
            stderr=stderr,
            duration_s=time.monotonic() - started,
        )

    async def start_exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
        tty: bool = False,
    ) -> ExecutionHandle:
        binary, args = split_command(command)
        box = await self._ensure_box()
        try:
            execution = await box.exec(
                binary,
                args,
                self._env(env),
                tty=tty,
                user=user,
                timeout_secs=timeout_s,
                cwd=cwd,
            )
        except Exception as exc:
            raise SandboxUnavailableError(f"VM execution failed: {exc}") from exc
        return cast(ExecutionHandle, execution)

    # -- filesystem ----------------------------------------------------------

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        info = await self.stat(path)
        if info is None:
            raise FileNotFoundError(path)
        if info.size > _DIRECT_MAX_BYTES:
            if max_bytes is not None and info.size > max_bytes:
                raise ValueError(f"file is too large: {info.size} bytes")
            return await self._copy_out_bytes(path)
        result = await self.exec(("base64", "-w0", "--", path), cwd="/")
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"could not read {path}")
        data = base64.b64decode(result.stdout.strip())
        if max_bytes is not None and len(data) > max_bytes:
            return data[:max_bytes]
        return data

    async def _copy_out_bytes(self, path: str) -> bytes:
        target = self._staging / f"{uuid.uuid4().hex}.bin"
        await self.copy_out(path, str(target))
        try:
            return target.read_bytes()
        finally:
            target.unlink(missing_ok=True)

    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        mode: int | None = None,
        append: bool = False,
    ) -> None:
        redirect = ">>" if append else ">"
        script = (
            'mkdir -p -- "$(dirname -- "$1")" && '
            f'base64 -d {redirect} "$1"'
        )
        execution = await self.start_exec(("sh", "-c", script, "sh", path), cwd="/")
        try:
            stdin = execution.stdin()
            if stdin is None:
                raise OSError("VM process has no stdin")
            encoded = base64.b64encode(data)
            chunk = 512 * 1024
            for offset in range(0, len(encoded), chunk):
                await cast(Any, stdin).send_input(encoded[offset: offset + chunk] + b"\n")
            await cast(Any, stdin).close()
            await execution.wait()
        finally:
            with suppress(Exception):
                await execution.kill()
        if mode is not None:
            await self.exec(("chmod", oct(mode & 0o7777)[2:], "--", path), cwd="/")

    async def list_dir(self, path: str) -> list[FileInfo]:
        result = await self.exec(("python3", "-c", _LIST_SCRIPT, path), cwd="/")
        if result.exit_code != 0:
            raise FileNotFoundError(path)
        return [_file_info(line) for line in result.stdout.splitlines() if line.strip()]

    async def stat(self, path: str) -> FileInfo | None:
        result = await self.exec(("python3", "-c", _STAT_SCRIPT, path), cwd="/")
        if result.exit_code != 0:
            return None
        try:
            return _file_info(result.stdout.strip())
        except (ValueError, KeyError):
            return None

    async def realpath(self, path: str) -> str:
        result = await self.exec(
            ("python3", "-c", "import os,sys; print(os.path.realpath(sys.argv[1]))", path),
            cwd="/",
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"could not resolve {path}")
        return result.stdout.strip()

    async def mkdir(self, path: str, *, parents: bool = True) -> None:
        args = ["-p", "--", path] if parents else ["--", path]
        result = await self.exec(("mkdir", *args), cwd="/")
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"could not create {path}")

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        args = ["-rf", "--", path] if recursive else ["-f", "--", path]
        result = await self.exec(("rm", *args), cwd="/")
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"could not remove {path}")

    async def rename(self, source: str, target: str) -> None:
        result = await self.exec(("mv", "-f", "--", source, target), cwd="/")
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"could not rename {source}")

    # -- controlled host transfer -------------------------------------------

    def host_staging_dir(self) -> str:
        return str(self._staging)

    async def copy_in(self, host_path: str, vm_path: str) -> None:
        # The runtime stages a tar under its own home; a home recreated after a
        # delete can be missing it, which fails the copy with ENOENT.
        staging = self.data_dir() / "tmp"
        if not staging.is_dir():
            staging.mkdir(parents=True, exist_ok=True)
        box = await self._ensure_box()
        try:
            await box.copy_in(host_path, vm_path)
        except Exception as exc:
            raise SandboxUnavailableError(f"import into the VM failed: {exc}") from exc

    async def copy_out(self, vm_path: str, host_path: str) -> None:
        box = await self._ensure_box()
        try:
            await box.copy_out(vm_path, host_path)
        except Exception as exc:
            raise SandboxUnavailableError(f"export from the VM failed: {exc}") from exc

    # -- networking ----------------------------------------------------------

    async def forwarded_port(self, guest_port: int) -> int | None:
        if self._box is None:
            return None
        try:
            info = await self._box.info()
            network = getattr(info, "network", None)
            for published in getattr(network, "published_ports", ()) or ():
                if int(getattr(published, "guest_port", -1)) == guest_port:
                    port = int(getattr(published, "host_port", 0)) or None
                    if port is not None:
                        return port
        except Exception:
            logger.debug("Could not read published ports for the Yoreni VM")
        return await self._ensure_tunnel(guest_port)

    async def _ensure_tunnel(self, guest_port: int) -> int | None:
        """Create one loopback-only forwarder when a restart lost its port map."""
        existing = self._tunnels.get(guest_port)
        if existing is not None:
            try:
                return int(existing.local_addr().port) or None
            except Exception:
                self._tunnels.pop(guest_port, None)
        try:
            import boxlite
            from boxlite import constants as boxlite_constants

            tunnel = await self._box.network.tunnel(
                (boxlite_constants.GUEST_IP, guest_port)
            )
            forwarder = await tunnel.forward(
                boxlite.SocketAddress.tcp("127.0.0.1", 0)
            )
            self._tunnels[guest_port] = forwarder
            return int(forwarder.local_addr().port) or None
        except Exception as exc:
            logger.warning("Could not forward VM port {}: {}", guest_port, exc)
            return None

    async def _close_tunnels(self) -> None:
        for forwarder in list(self._tunnels.values()):
            with suppress(Exception):
                await forwarder.close()
        self._tunnels.clear()

    async def close(self) -> None:
        await self._close_tunnels()
        # The VM is deliberately not stopped: it is Yoreni's persistent
        # computer. Only the local staging directory is released.
        for child in self._staging.glob("*"):
            child.unlink(missing_ok=True)
        try:
            self._staging.rmdir()
        except OSError:
            pass


async def _collect(stream: Any) -> str:
    if stream is None:
        return ""
    parts: list[str] = []
    async for line in stream:
        parts.append(line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line)
    return "".join(parts)


def _file_info(raw: str) -> FileInfo:
    payload = json.loads(raw)
    return FileInfo(
        path=str(payload["path"]),
        is_dir=bool(payload["is_dir"]),
        is_file=bool(payload["is_file"]),
        is_symlink=bool(payload["is_symlink"]),
        size=int(payload["size"]),
        mode=int(payload["mode"]),
        mtime_s=float(payload["mtime_s"]),
    )


async def read_stream(stream: AsyncIterator[str | bytes] | None) -> str:
    if stream is None:
        return ""
    parts: list[str] = []
    async for line in stream:
        parts.append(line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line)
    return "".join(parts)
