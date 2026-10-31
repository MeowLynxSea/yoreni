"""A host-backed backend used only by the test suite.

Production code never constructs this backend. It lets the existing tool
contract tests exercise the same gateway interface without a hypervisor.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from yoreni.sandbox.backend import ExecutionHandle, split_command
from yoreni.sandbox.models import ExecResult, FileInfo, SandboxHealth, SandboxStatus


class _LocalHandle:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    def stdout(self) -> AsyncIterator[bytes] | None:
        stream = self._process.stdout
        if stream is None:
            return None

        async def lines() -> AsyncIterator[bytes]:
            while line := await stream.readline():
                yield line

        return lines()

    def stderr(self) -> AsyncIterator[bytes] | None:
        stream = self._process.stderr
        if stream is None:
            return None

        async def lines() -> AsyncIterator[bytes]:
            while line := await stream.readline():
                yield line

        return lines()

    def stdin(self) -> Any:
        return self._process.stdin

    async def wait(self) -> Any:
        code = await self._process.wait()
        return type("Result", (), {"exit_code": code})()

    async def kill(self) -> None:
        if self._process.returncode is None:
            self._process.kill()
            await self._process.wait()


class LocalBackend:
    name = "test-local"

    def __init__(self) -> None:
        self._running = True
        self._staging = Path(tempfile.mkdtemp(prefix="yoreni-test-sandbox-"))

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def restart(self) -> None:
        self._running = True

    async def status(self) -> SandboxStatus:
        return SandboxStatus(
            backend=self.name,
            box_id="test",
            name="test",
            state="running" if self._running else "stopped",
            workspace="/",
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
        del user
        started = asyncio.get_running_loop().time()
        binary, args = split_command(command)
        merged = {"PATH": os.environ.get("PATH", ""), **dict(env or {})}
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
            cwd=cwd,
            env=merged,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
            timed_out = False
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            timed_out = True
        return ExecResult(
            command=binary,
            exit_code=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_s=asyncio.get_running_loop().time() - started,
            timed_out=timed_out,
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
        del timeout_s, user, tty
        binary, args = split_command(command)
        merged = {"PATH": os.environ.get("PATH", ""), **dict(env or {})}
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
            cwd=cwd,
            env=merged,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return cast(ExecutionHandle, _LocalHandle(process))

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        data = await asyncio.to_thread(Path(path).read_bytes)
        return data[:max_bytes] if max_bytes is not None else data

    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        mode: int | None = None,
        append: bool = False,
    ) -> None:
        target = Path(path)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("ab" if append else "wb") as handle:
                handle.write(data)
            if mode is not None:
                target.chmod(mode)

        await asyncio.to_thread(write)

    async def list_dir(self, path: str) -> list[FileInfo]:
        def read() -> list[FileInfo]:
            return [_info(item) for item in sorted(Path(path).iterdir())]

        return await asyncio.to_thread(read)

    async def stat(self, path: str) -> FileInfo | None:
        def read() -> FileInfo | None:
            try:
                return _info(Path(path))
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(read)

    async def realpath(self, path: str) -> str:
        return await asyncio.to_thread(lambda: str(Path(path).resolve(strict=False)))

    async def mkdir(self, path: str, *, parents: bool = True) -> None:
        await asyncio.to_thread(lambda: Path(path).mkdir(parents=parents, exist_ok=True))

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        target = Path(path)
        if recursive:
            await asyncio.to_thread(lambda: shutil.rmtree(target, ignore_errors=True))
        else:
            await asyncio.to_thread(lambda: target.unlink(missing_ok=True))

    async def rename(self, source: str, target: str) -> None:
        await asyncio.to_thread(lambda: Path(source).rename(target))

    async def forwarded_port(self, guest_port: int) -> int | None:
        del guest_port
        return None

    def host_staging_dir(self) -> str:
        return str(self._staging)

    async def copy_in(self, host_path: str, vm_path: str) -> None:
        await asyncio.to_thread(lambda: shutil.copy2(host_path, vm_path))

    async def copy_out(self, vm_path: str, host_path: str) -> None:
        await asyncio.to_thread(lambda: shutil.copy2(vm_path, host_path))

    async def close(self) -> None:
        await asyncio.to_thread(lambda: shutil.rmtree(self._staging, ignore_errors=True))


def _info(path: Path) -> FileInfo:
    stat = path.lstat()
    return FileInfo(
        path=str(path),
        is_dir=path.is_dir(),
        is_file=path.is_file(),
        is_symlink=path.is_symlink(),
        size=stat.st_size,
        mode=stat.st_mode & 0o7777,
        mtime_s=stat.st_mtime,
    )
