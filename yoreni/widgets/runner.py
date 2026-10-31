"""Run a widget's backend command inside the execution VM."""

from __future__ import annotations

import posixpath
import time
from pathlib import Path

from yoreni.sandbox.errors import SandboxError
from yoreni.sandbox.gateway import SandboxGateway, get_sandbox_gateway
from yoreni.widgets.types import (
    MAX_STDOUT_BYTES,
    WidgetDefinition,
    WidgetError,
    WidgetRunResult,
    parse_stdout_json,
)

_WIDGETS_DIR = ".yoreni/widgets"


class WidgetRunner:
    """Execute one widget command in the VM copy of its storage directory."""

    async def run(
        self,
        definition: WidgetDefinition,
        *,
        widget_dir: Path,
    ) -> WidgetRunResult:
        argv = definition.command
        if not argv:
            return WidgetRunResult(error="widget has no command")
        started = time.monotonic()
        try:
            gateway = get_sandbox_gateway()
            cwd = await _stage(gateway, definition.id, widget_dir)
            result = await gateway.exec(
                list(argv),
                cwd=cwd,
                env={"HOME": cwd, "TMPDIR": cwd},
                timeout_s=definition.timeout_seconds,
            )
        except (SandboxError, OSError) as exc:
            return WidgetRunResult(error=str(exc), duration_ms=_elapsed_ms(started))
        duration_ms = _elapsed_ms(started)
        # The VM enforces the budget itself, so a run that used all of it was
        # killed rather than finished.
        if result.timed_out or result.duration_s >= definition.timeout_seconds:
            return WidgetRunResult(
                error=f"command timed out after {definition.timeout_seconds}s",
                duration_ms=duration_ms,
            )
        if len(result.stdout.encode("utf-8", "replace")) > MAX_STDOUT_BYTES:
            return WidgetRunResult(
                error=f"command output exceeded {MAX_STDOUT_BYTES // 1024} KB",
                duration_ms=duration_ms,
            )
        if result.exit_code != 0:
            stderr = result.stderr.strip()
            suffix = f": {stderr[-400:]}" if stderr else ""
            return WidgetRunResult(
                error=f"command exited with status {result.exit_code}{suffix}",
                duration_ms=duration_ms,
            )
        try:
            data = parse_stdout_json(result.stdout)
        except WidgetError as exc:
            return WidgetRunResult(error=str(exc), duration_ms=duration_ms)
        return WidgetRunResult(data=data, duration_ms=duration_ms)


async def _stage(gateway: SandboxGateway, widget_id: str, widget_dir: Path) -> str:
    """Copy the widget's files into its own directory inside the VM workspace."""
    target = posixpath.join(gateway.config.workspace, _WIDGETS_DIR, widget_id)
    await gateway.mkdir(target)
    for path in sorted(widget_dir.iterdir()):
        if path.is_file():
            await gateway.write_file(posixpath.join(target, path.name), path.read_bytes())
    return target


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
