"""Run a widget's backend command inside its storage directory."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import time
from contextlib import suppress
from pathlib import Path

from loguru import logger

from yoreni.agent.tools.sandbox import wrap_command
from yoreni.widgets.types import (
    MAX_STDOUT_BYTES,
    WidgetDefinition,
    WidgetError,
    WidgetRunResult,
    parse_stdout_json,
)

_STDERR_BYTES = 16 * 1024
_READ_CHUNK = 64 * 1024


class _OutputLimitError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit


class WidgetRunner:
    """Execute one widget command with the gateway's exec sandbox policy."""

    def __init__(self, *, sandbox: str = "") -> None:
        self._sandbox = sandbox.strip()

    async def run(
        self,
        definition: WidgetDefinition,
        *,
        widget_dir: Path,
        workspace: Path | None,
    ) -> WidgetRunResult:
        argv = definition.command
        if not argv:
            return WidgetRunResult(error="widget has no command")
        started = time.monotonic()
        command = self._prepare(argv, widget_dir, workspace)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(widget_dir),
                env=_build_env(widget_dir),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError:
            return WidgetRunResult(
                error=f"executable not found: {argv[0]}", duration_ms=_elapsed_ms(started),
            )
        except OSError as exc:
            return WidgetRunResult(
                error=f"could not start command: {exc}", duration_ms=_elapsed_ms(started),
            )

        stdout_task = asyncio.create_task(_read_capped(process.stdout, MAX_STDOUT_BYTES))
        stderr_task = asyncio.create_task(_read_capped(process.stderr, _STDERR_BYTES))
        tasks = [stdout_task, stderr_task]
        deadline = time.monotonic() + definition.timeout_seconds
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks), timeout=definition.timeout_seconds,
            )
            await asyncio.wait_for(
                process.wait(), timeout=max(deadline - time.monotonic(), 0.001),
            )
        except asyncio.TimeoutError:
            await _terminate(process)
            return WidgetRunResult(
                error=f"command timed out after {definition.timeout_seconds}s",
                duration_ms=_elapsed_ms(started),
            )
        except _OutputLimitError as exc:
            await _terminate(process)
            return WidgetRunResult(
                error=f"command output exceeded {exc.limit // 1024} KB",
                duration_ms=_elapsed_ms(started),
            )
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

        if process.returncode != 0:
            stderr = stderr_task.result().decode("utf-8", errors="replace").strip()
            suffix = f": {stderr[-400:]}" if stderr else ""
            return WidgetRunResult(
                error=f"command exited with status {process.returncode}{suffix}",
                duration_ms=_elapsed_ms(started),
            )
        stdout = stdout_task.result().decode("utf-8", errors="replace")
        try:
            data = parse_stdout_json(stdout)
        except WidgetError as exc:
            return WidgetRunResult(error=str(exc), duration_ms=_elapsed_ms(started))
        return WidgetRunResult(data=data, duration_ms=_elapsed_ms(started))

    def _prepare(
        self, argv: tuple[str, ...], widget_dir: Path, workspace: Path | None,
    ) -> list[str]:
        if not self._sandbox:
            return list(argv)
        if os.name == "nt":
            logger.warning("widget command ran without a sandbox: sandbox backends require POSIX")
            return list(argv)
        bind: list[str] = []
        if workspace is not None:
            try:
                resolved = workspace.expanduser().resolve(strict=False)
            except OSError:
                resolved = None
            if (
                resolved is not None
                and resolved.is_dir()
                and not resolved.is_relative_to(widget_dir)
            ):
                bind.append(str(resolved))
        wrapped = wrap_command(
            self._sandbox,
            shlex.join(argv),
            str(widget_dir),
            str(widget_dir),
            sandbox_ro_binds=bind or None,
        )
        return shlex.split(wrapped)


async def _read_capped(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise _OutputLimitError(limit)
        chunks.append(chunk)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        with suppress(OSError):
            process.kill()
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


def _build_env(widget_dir: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(widget_dir),
    }
    for key in ("LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if os.name == "nt":
        env["USERPROFILE"] = str(widget_dir)
        env["TEMP"] = str(widget_dir)
        env["TMP"] = str(widget_dir)
    else:
        env["TMPDIR"] = str(widget_dir)
    return env


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
