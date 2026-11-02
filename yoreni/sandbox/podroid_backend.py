"""Podroid-backed execution inside an Android Linux VM.

Podroid is a separate Android app that boots a real Alpine VM (QEMU or AVF)
and exposes it over SSH on loopback. It owns the kernel boundary, so this
backend only speaks SSH and SFTP: commands go through exec channels, files
through SFTP primitives instead of spawning interpreters, and the desktop
WebSocket reaches the guest through a direct-tcpip channel rather than a port
published to the network.

The VM is started and stopped through an injected controller. On a desktop that
is an `adb shell am broadcast`; inside the Android app it is a Java bridge to
the same broadcast. Nothing here assumes either.
"""

from __future__ import annotations

import asyncio
import errno
import posixpath
import shlex
import stat
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import paramiko
from loguru import logger

from yoreni.sandbox.backend import ExecutionHandle, split_command
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.models import ExecResult, FileInfo, SandboxHealth, SandboxStatus

# Podroid's receiver accepts these from any app; see its VmControlReceiver.
START_ACTION = "com.excp.podroid.action.START_VM"
STOP_ACTION = "com.excp.podroid.action.STOP_VM"
RECEIVER = "com.excp.podroid/.service.VmControlReceiver"

VmControl = Callable[[str], Awaitable[None]]


class AdbVmControl:
    """Start and stop Podroid from an adb-capable host."""

    def __init__(self, adb: str = "adb") -> None:
        self._adb = adb

    async def __call__(self, action: str) -> None:
        broadcast = START_ACTION if action == "start" else STOP_ACTION
        process = await asyncio.create_subprocess_exec(
            self._adb,
            "shell",
            "am",
            "broadcast",
            "-a",
            broadcast,
            "-n",
            RECEIVER,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        text = (out + err).decode("utf-8", "replace")
        if process.returncode != 0 or "result=0" not in text:
            raise SandboxUnavailableError(
                f"Podroid did not accept the {action} broadcast: {text.strip()[:200]}"
            )


class HttpVmControl:
    """Ask the Android host to start or stop the VM over loopback.

    Inside an Android app the gateway lives in a Linux rootfs and cannot send an
    Android broadcast, so the host exposes one on 127.0.0.1. On a desktop the
    same interface is served by whatever started the VM.
    """

    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/")

    async def __call__(self, action: str) -> None:
        await asyncio.to_thread(self._send, action)

    def _send(self, action: str) -> None:
        request = urllib.request.Request(
            f"{self._url}/{action}", method="POST", data=b""
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                reply = response.read().decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise SandboxUnavailableError(
                detail or f"The Android host refused to {action} the VM"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise SandboxUnavailableError(
                f"The Android host did not answer the {action} request: {exc}"
            ) from exc
        if not reply.startswith("ok"):
            raise SandboxUnavailableError(
                reply or f"The Android host refused to {action} the VM"
            )


def _shell(
    command: str | Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str] | None,
    user: str | None,
) -> str:
    """Render one exec as a shell line, the way the VM shell sees it."""
    binary, args = split_command(command)
    body = args[-1] if binary == "sh" and args[:1] == ["-lc"] else shlex.join([binary, *args])
    prefix = ""
    for key, value in (env or {}).items():
        prefix += f"export {key}={shlex.quote(value)}; "
    if cwd:
        prefix += f"cd {shlex.quote(cwd)} && "
    if user and user != "root":
        return f"su -s /bin/sh {shlex.quote(user)} -c {shlex.quote(prefix + body)}"
    return prefix + body


class _SshProcess:
    """A live exec channel exposed as the gateway's handle contract."""

    def __init__(self, channel: paramiko.Channel, loop: asyncio.AbstractEventLoop) -> None:
        self._channel = channel
        self._loop = loop
        self._stdout: asyncio.Queue[bytes | None] | None = None
        self._stderr: asyncio.Queue[bytes | None] | None = None

    def _pump(self, source: Any, queue: asyncio.Queue[bytes | None]) -> None:
        try:
            while chunk := source.read(65536):
                self._loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(queue.put_nowait, None)

    def _queue(self, stream: Any) -> asyncio.Queue[bytes | None]:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        asyncio.ensure_future(asyncio.to_thread(self._pump, stream, queue))
        return queue

    def stdout(self) -> AsyncIterator[bytes] | None:
        if self._stdout is None:
            self._stdout = self._queue(self._channel.makefile("rb"))
        queue = self._stdout

        async def lines() -> AsyncIterator[bytes]:
            buffer = b""
            while True:
                chunk = await queue.get()
                if chunk is None:
                    if buffer:
                        yield buffer
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    yield line + b"\n"

        return lines()

    def stderr(self) -> AsyncIterator[bytes] | None:
        if self._stderr is None:
            self._stderr = self._queue(self._channel.makefile_stderr("rb"))
        queue = self._stderr

        async def lines() -> AsyncIterator[bytes]:
            while (chunk := await queue.get()) is not None:
                yield chunk

        return lines()

    def stdin(self) -> Any:
        return self._channel.makefile("wb")

    async def wait(self) -> Any:
        code = await asyncio.to_thread(self._channel.recv_exit_status)
        return type("Result", (), {"exit_code": code})()

    async def kill(self) -> None:
        self._channel.close()


class PodroidBackend:
    name = "podroid"

    def __init__(
        self,
        config: SandboxConfig,
        *,
        control: VmControl | None = None,
    ) -> None:
        self._config = config
        # The VM's own X server is :0, so the desktop services must attach
        # there rather than at the webtop default.
        if config.display == ":1":
            config.display = config.podroid_display
        self._control = control
        self._client: paramiko.SSHClient | None = None
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._lock = asyncio.Lock()
        self._staging = Path(tempfile.mkdtemp(prefix="yoreni-podroid-"))
        self._tunnels: dict[int, Any] = {}
        self._started_at_ms: int | None = None

    # -- connection ----------------------------------------------------------

    def _connect_blocking(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        # The VM is reachable only on loopback, and its host key changes when
        # Podroid rebuilds the VM; a pinned key would break every rebuild.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect: dict[str, Any] = {
            "hostname": self._config.podroid_host,
            "port": self._config.podroid_port,
            "username": self._config.podroid_user,
            "timeout": 10,
            "banner_timeout": 20,
            "auth_timeout": 20,
            "allow_agent": False,
            "look_for_keys": False,
        }
        password = self._config.podroid_password
        key = self._config.podroid_key_path
        if key:
            connect["key_filename"] = key
        elif password:
            connect["password"] = password
        client.connect(**connect)
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        return client

    async def _ensure_client(self) -> paramiko.SSHClient:
        async with self._lock:
            if self._client is not None:
                transport = self._transport
                if transport is not None and transport.is_active():
                    return self._client
                self._drop_client()
            try:
                client = await asyncio.to_thread(self._connect_blocking)
            except (OSError, paramiko.SSHException) as exc:
                raise SandboxUnavailableError(
                    "The Podroid VM is not reachable over SSH "
                    f"({self._config.podroid_host}:{self._config.podroid_port}): {exc}"
                ) from exc
            self._client = client
            self._transport = client.get_transport()
            self._started_at_ms = int(time.time() * 1000)
            return client

    def _drop_client(self) -> None:
        if self._sftp is not None:
            with suppress(Exception):
                self._sftp.close()
        self._sftp = None
        if self._client is not None:
            with suppress(Exception):
                self._client.close()
        self._client = None
        self._transport = None

    async def _sftp_client(self) -> paramiko.SFTPClient:
        await self._ensure_client()
        if self._sftp is not None:
            return self._sftp
        assert self._client is not None

        def open_sftp() -> paramiko.SFTPClient:
            assert self._client is not None
            return self._client.open_sftp()

        try:
            self._sftp = await asyncio.to_thread(open_sftp)
        except (OSError, paramiko.SSHException) as exc:
            raise SandboxUnavailableError(f"Could not open an SFTP channel: {exc}") from exc
        return self._sftp

    # -- lifecycle -----------------------------------------------------------

    async def _control_vm(self, action: str) -> None:
        if self._control is None:
            raise SandboxUnavailableError(
                "Starting the Podroid VM needs a controller: the Android app "
                "injects one, and a desktop needs adb on PATH."
            )
        await self._control(action)

    async def prepare(self) -> None:
        await self._ensure_client()

    async def start(self) -> None:
        if await self._reachable():
            return
        # Android freezes an idle background app, and the first broadcast can be
        # dropped before the receiver's process is resumed; one repeat is what
        # makes a cold start reliable without hammering the VM.
        for attempt in range(2):
            await self._control_vm("start")
            deadline = time.monotonic() + self._config.podroid_start_timeout_s / 2
            while time.monotonic() < deadline:
                if await self._reachable():
                    return
                await asyncio.sleep(1.5)
            logger.info("Podroid did not answer after start attempt {}", attempt + 1)
        raise SandboxUnavailableError(
            "The Podroid VM did not answer on SSH within "
            f"{self._config.podroid_start_timeout_s}s after it was started. "
            "Open Podroid once so Android resumes it, then retry."
        )

    async def _reachable(self) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._config.podroid_host, self._config.podroid_port),
                timeout=3,
            )
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def stop(self) -> None:
        if self._control is None:
            raise SandboxUnavailableError("Stopping the Podroid VM needs a controller")
        self._drop_client()
        await self._control_vm("stop")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def destroy(self) -> None:
        # Podroid owns the VM disk; Yoreni owns only what it wrote into the
        # workspace, so a destroy is the same as clearing that directory.
        await self.stop()
        with suppress(Exception):
            await self.remove(self._config.workspace, recursive=True)
        with suppress(Exception):
            await self.mkdir(self._config.workspace)

    async def status(self) -> SandboxStatus:
        running = await self._reachable()
        return SandboxStatus(
            backend=self.name,
            box_id=self._config.box_name,
            name=self._config.box_name,
            state="running" if running else "stopped",
            workspace=self._config.workspace,
            health=SandboxHealth(running, False, False),
        )

    # -- execution -----------------------------------------------------------

    async def exec(
        self,
        command: str | Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        user: str | None = None,
    ) -> ExecResult:
        client = await self._ensure_client()
        line = _shell(command, cwd=cwd, env=env, user=user)
        started = asyncio.get_running_loop().time()

        def run() -> tuple[int, bytes, bytes]:
            stdin, stdout, stderr = client.exec_command(
                line, timeout=timeout_s, get_pty=False
            )
            del stdin
            out = stdout.read()
            err = stderr.read()
            return stdout.channel.recv_exit_status(), out, err

        try:
            code, out, err = await asyncio.wait_for(
                asyncio.to_thread(run), timeout_s + 10 if timeout_s else None
            )
        except asyncio.TimeoutError:
            return ExecResult(
                command=line,
                exit_code=124,
                stdout="",
                stderr=f"command timed out after {timeout_s}s",
                duration_s=asyncio.get_running_loop().time() - started,
                timed_out=True,
            )
        except (OSError, paramiko.SSHException) as exc:
            self._drop_client()
            raise SandboxUnavailableError(f"VM execution failed: {exc}") from exc
        return ExecResult(
            command=line,
            exit_code=code,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            duration_s=asyncio.get_running_loop().time() - started,
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
        client = await self._ensure_client()
        line = _shell(command, cwd=cwd, env=env, user=user)
        timeout = timeout_s if timeout_s is not None else None

        def open_channel() -> paramiko.Channel:
            transport = client.get_transport()
            if transport is None:
                raise SandboxUnavailableError("The SSH transport is gone")
            channel = transport.open_session(timeout=timeout)
            if tty:
                channel.get_pty()
            channel.exec_command(line)
            return channel

        try:
            channel = await asyncio.to_thread(open_channel)
        except (OSError, paramiko.SSHException) as exc:
            self._drop_client()
            raise SandboxUnavailableError(f"VM execution failed: {exc}") from exc
        return cast(
            ExecutionHandle,
            _SshProcess(channel, asyncio.get_running_loop()),
        )

    # -- filesystem ----------------------------------------------------------

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        sftp = await self._sftp_client()

        def read() -> bytes:
            with sftp.open(path, "rb") as handle:
                handle.prefetch()
                return handle.read() if max_bytes is None else handle.read(max_bytes)

        try:
            return await asyncio.to_thread(read)
        except FileNotFoundError:
            raise
        except (OSError, paramiko.SSHException) as exc:
            raise OSError(str(exc)) from exc

    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        mode: int | None = None,
        append: bool = False,
    ) -> None:
        sftp = await self._sftp_client()

        def write() -> None:
            parent = posixpath.dirname(path)
            if parent and parent != "/":
                self._mkdir_blocking(sftp, parent)
            with sftp.open(path, "ab" if append else "wb") as handle:
                handle.write(data)
            if mode is not None:
                sftp.chmod(path, mode & 0o7777)

        try:
            await asyncio.to_thread(write)
        except (OSError, paramiko.SSHException) as exc:
            raise OSError(str(exc)) from exc

    async def list_dir(self, path: str) -> list[FileInfo]:
        sftp = await self._sftp_client()

        def read() -> list[FileInfo]:
            entries = []
            for attr in sftp.listdir_attr(path):
                entries.append(_file_info(posixpath.join(path, attr.filename), attr))
            entries.sort(key=lambda item: item.path)
            return entries

        try:
            return await asyncio.to_thread(read)
        except FileNotFoundError:
            raise
        except (OSError, paramiko.SSHException) as exc:
            raise FileNotFoundError(path) from exc

    async def stat(self, path: str) -> FileInfo | None:
        sftp = await self._sftp_client()

        def read() -> FileInfo | None:
            try:
                return _file_info(path, sftp.lstat(path))
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(read)

    async def realpath(self, path: str) -> str:
        sftp = await self._sftp_client()

        def resolve() -> str:
            try:
                return sftp.normalize(path)
            except FileNotFoundError:
                # SFTP realpath is lexical for a path that does not exist yet,
                # and callers rely on that when they check containment first.
                return posixpath.normpath(posixpath.join("/", path))

        return await asyncio.to_thread(resolve)

    async def mkdir(self, path: str, *, parents: bool = True) -> None:
        sftp = await self._sftp_client()

        def create() -> None:
            if parents:
                self._mkdir_blocking(sftp, path)
            else:
                sftp.mkdir(path)

        try:
            await asyncio.to_thread(create)
        except (OSError, paramiko.SSHException) as exc:
            raise OSError(str(exc)) from exc

    def _mkdir_blocking(self, sftp: paramiko.SFTPClient, path: str) -> None:
        missing = []
        current = path
        while current not in ("", "/"):
            try:
                sftp.stat(current)
                break
            except FileNotFoundError:
                missing.append(current)
                current = posixpath.dirname(current)
        for item in reversed(missing):
            sftp.mkdir(item)

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        sftp = await self._sftp_client()

        def remove() -> None:
            if not recursive:
                with suppress(FileNotFoundError):
                    sftp.remove(path)
                return
            self._rmtree_blocking(sftp, path)

        try:
            await asyncio.to_thread(remove)
        except (OSError, paramiko.SSHException) as exc:
            raise OSError(str(exc)) from exc

    def _rmtree_blocking(self, sftp: paramiko.SFTPClient, path: str) -> None:
        try:
            mode = sftp.lstat(path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(mode):
            sftp.remove(path)
            return
        for attr in sftp.listdir_attr(path):
            self._rmtree_blocking(sftp, posixpath.join(path, attr.filename))
        sftp.rmdir(path)

    async def rename(self, source: str, target: str) -> None:
        sftp = await self._sftp_client()
        try:
            await asyncio.to_thread(sftp.posix_rename, source, target)
        except OSError as exc:
            if exc.errno not in (errno.ENOSYS, getattr(errno, "EOPNOTSUPP", 95)):
                raise OSError(str(exc)) from exc
            await asyncio.to_thread(sftp.rename, source, target)

    # -- host transfer -------------------------------------------------------

    def host_staging_dir(self) -> str:
        return str(self._staging)

    async def copy_in(self, host_path: str, vm_path: str) -> None:
        sftp = await self._sftp_client()
        source = Path(host_path)

        def push() -> None:
            if source.is_dir():
                self._mkdir_blocking(sftp, vm_path)
                for item in sorted(source.rglob("*")):
                    relative = item.relative_to(source)
                    target = posixpath.join(vm_path, relative.as_posix())
                    if item.is_dir():
                        self._mkdir_blocking(sftp, target)
                    else:
                        sftp.put(str(item), target)

        try:
            await asyncio.to_thread(push)
        except (OSError, paramiko.SSHException) as exc:
            raise SandboxUnavailableError(f"import into the VM failed: {exc}") from exc

    async def copy_out(self, vm_path: str, host_path: str) -> None:
        sftp = await self._sftp_client()
        target = Path(host_path)
        root = vm_path[:-2] if vm_path.endswith("/.") else vm_path

        def pull() -> None:
            try:
                mode = sftp.lstat(root).st_mode
            except FileNotFoundError:
                raise
            if not stat.S_ISDIR(mode):
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(root, str(target))
                return
            target.mkdir(parents=True, exist_ok=True)
            self._pull_tree(sftp, root, target)

        try:
            await asyncio.to_thread(pull)
        except (OSError, paramiko.SSHException) as exc:
            raise SandboxUnavailableError(f"export from the VM failed: {exc}") from exc

    def _pull_tree(self, sftp: paramiko.SFTPClient, remote: str, local: Path) -> None:
        for attr in sftp.listdir_attr(remote):
            source = posixpath.join(remote, attr.filename)
            destination = local / attr.filename
            if stat.S_ISDIR(attr.st_mode):
                destination.mkdir(parents=True, exist_ok=True)
                self._pull_tree(sftp, source, destination)
            elif stat.S_ISREG(attr.st_mode):
                sftp.get(source, str(destination))
            else:
                logger.debug("Skipping non-regular file {}", source)

    # -- networking ----------------------------------------------------------

    async def forwarded_port(self, guest_port: int) -> int | None:
        existing = self._tunnels.get(guest_port)
        if existing is not None:
            return int(existing.sockets[0].getsockname()[1])
        await self._ensure_client()
        try:
            server = await asyncio.start_server(
                lambda reader, writer: self._bridge(guest_port, reader, writer),
                host="127.0.0.1",
                port=0,
            )
        except OSError as exc:
            logger.warning("Could not forward VM port {}: {}", guest_port, exc)
            return None
        self._tunnels[guest_port] = server
        return int(server.sockets[0].getsockname()[1])

    async def _bridge(
        self,
        guest_port: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Pipe one loopback connection into a direct-tcpip channel."""
        transport = self._transport
        if transport is None:
            writer.close()
            return
        try:
            channel = await asyncio.to_thread(
                transport.open_channel,
                "direct-tcpip",
                ("127.0.0.1", guest_port),
                writer.get_extra_info("peername") or ("127.0.0.1", 0),
            )
        except Exception as exc:
            logger.debug("Could not open a tunnel to guest port {}: {}", guest_port, exc)
            writer.close()
            return

        async def to_guest() -> None:
            while data := await reader.read(32768):
                await asyncio.to_thread(channel.sendall, data)
            channel.shutdown_write()

        async def from_guest() -> None:
            while True:
                data = await asyncio.to_thread(channel.recv, 32768)
                if not data:
                    break
                writer.write(data)
                await writer.drain()

        try:
            await asyncio.gather(to_guest(), from_guest())
        except (OSError, paramiko.SSHException):
            pass
        finally:
            with suppress(Exception):
                channel.close()
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _close_tunnels(self) -> None:
        for server in list(self._tunnels.values()):
            server.close()
            with suppress(Exception):
                await server.wait_closed()
        self._tunnels.clear()

    async def close(self) -> None:
        await self._close_tunnels()
        # The VM keeps running: it is the user's persistent computer.
        self._drop_client()


def _file_info(path: str, attr: paramiko.SFTPAttributes) -> FileInfo:
    mode = attr.st_mode or 0
    return FileInfo(
        path=path,
        is_dir=stat.S_ISDIR(mode),
        is_file=stat.S_ISREG(mode),
        is_symlink=stat.S_ISLNK(mode),
        size=attr.st_size or 0,
        mode=mode & 0o7777,
        mtime_s=float(attr.st_mtime or 0),
    )
