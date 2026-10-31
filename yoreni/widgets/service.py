"""Widget lifecycle, change notification, and periodic refresh."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from loguru import logger

from yoreni.runtime_admission import RuntimeAdmission, RuntimeMaintenanceError
from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.store import WidgetStore
from yoreni.widgets.types import (
    MAX_WIDGETS,
    WIDGET_FILE_RE,
    WidgetChange,
    WidgetDefinition,
    WidgetError,
    WidgetNotFoundError,
    WidgetPatch,
    WidgetRunResult,
    WidgetSnapshot,
    WidgetSpec,
    WidgetSummary,
    now_iso,
    now_ms,
    validate_command,
    validate_data,
    validate_description,
    validate_html,
    validate_refresh_interval,
    validate_script,
    validate_timeout,
    validate_title,
    validate_view,
    validate_widget_filename,
    validate_widget_id,
)

_TICK_CEILING_MS = 300_000
_TICK_FLOOR_MS = 1_000


class WidgetService:
    """Own widget definitions, cached data, and the refresh loop."""

    def __init__(
        self,
        store: WidgetStore,
        *,
        runner: WidgetRunner,
        workspace: Path,
        admission: RuntimeAdmission | None = None,
        on_change: Callable[[WidgetChange], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._workspace = workspace.expanduser().resolve(strict=False)
        self._admission = admission
        self._on_change = on_change
        self._definition_lock = asyncio.Lock()
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._next_runs: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None

    def set_sandbox(self, sandbox: str) -> None:
        """Apply a new exec sandbox policy to future widget runs."""
        self._runner.set_sandbox(sandbox)

    def set_sandbox_enabled(self, enabled: bool) -> None:
        """Disable host widget commands while the VM owns execution."""
        self._runner.set_sandbox_enabled(enabled)

    # -- reads -------------------------------------------------------------

    def summaries(self) -> list[WidgetSummary]:
        summaries: list[WidgetSummary] = []
        for widget_id in self._store.ids():
            try:
                definition = self._store.read(widget_id)
            except WidgetError:
                logger.warning("skipping unreadable widget {}", widget_id)
                continue
            summaries.append(self._summary(definition))
        return summaries

    def snapshot(self, widget_id: str) -> WidgetSnapshot:
        definition = self._store.read(widget_id)
        summary = self._summary(definition)
        envelope = self._store.read_envelope(widget_id)
        data = envelope[0] if envelope else None
        html = self._store.read_html(widget_id) if definition.view == "html" else None
        return WidgetSnapshot(summary=summary, data=data, html=html)

    def script_source(self, widget_id: str) -> tuple[str, str] | None:
        """Return the widget's script filename and source, if the command runs one."""
        definition = self._store.read(widget_id)
        name = self._script_name(definition)
        if name is None:
            return None
        content = self._store.read_script(widget_id, name)
        return None if content is None else (name, content)

    # -- mutations ---------------------------------------------------------

    async def create(self, spec: WidgetSpec) -> tuple[WidgetDefinition, WidgetRunResult | None]:
        async with self._definition_lock:
            widget_id = validate_widget_id(spec.id)
            title = validate_title(spec.title)
            description = validate_description(spec.description)
            view = validate_view(spec.view)
            html, script, filename, command, timeout, every_seconds = _validate_spec(spec)
            if len(self._store.ids()) >= MAX_WIDGETS:
                raise WidgetError(f"at most {MAX_WIDGETS} widgets can be stored")
            if self._exists(widget_id):
                raise WidgetError(f"widget '{widget_id}' already exists")
            workspace = _validate_workspace(spec.workspace, self._workspace)
            stamp = now_iso()
            definition = WidgetDefinition(
                id=widget_id,
                title=title,
                description=description,
                view=view,
                command=command,
                timeout_seconds=timeout,
                every_seconds=every_seconds,
                workspace=workspace,
                created_at=stamp,
                updated_at=stamp,
            )
            self._store.prepare(widget_id)
            try:
                if html is not None:
                    self._store.write_html(widget_id, html)
                if script is not None:
                    self._store.write_script(widget_id, filename, script)
                self._store.write_definition(definition)
            except Exception:
                with suppress(WidgetError, OSError):
                    self._store.remove(widget_id)
                raise
            await self._notify(WidgetChange(widget_id, "created"))
            result = await self.refresh(widget_id) if command else None
            self._schedule(widget_id, every_seconds if command else None)
            return definition, result

    async def update(
        self, widget_id: str, patch: WidgetPatch,
    ) -> tuple[WidgetDefinition, WidgetRunResult | None]:
        async with self._definition_lock:
            current = self._store.read(widget_id)
            title = current.title if patch.title is None else validate_title(patch.title)
            description = (
                current.description
                if patch.description is None
                else validate_description(patch.description)
            )
            view = current.view if patch.view is None else validate_view(patch.view)
            html: str | None = None
            if patch.html is not None:
                if view != "html":
                    raise WidgetError("html is only allowed when view='html'")
                html = validate_html(patch.html)
            if view == "html" and html is None and self._store.read_html(widget_id) is None:
                raise WidgetError("html is required when view='html'")
            if patch.script_filename is not None and patch.script is None:
                raise WidgetError("script_filename requires script")
            script = validate_script(patch.script) if patch.script is not None else None
            filename = (
                validate_widget_filename(patch.script_filename)
                if patch.script_filename is not None
                else self._script_filename(current)
            )
            command = current.command
            if patch.command is not None:
                command = validate_command(patch.command)
            if command is None and script is not None and patch.command is None:
                command = ("python3", filename)
            timeout = (
                current.timeout_seconds
                if patch.timeout_seconds is None
                else validate_timeout(patch.timeout_seconds)
            )
            every_seconds = (
                current.every_seconds
                if patch.every_seconds is None
                else validate_refresh_interval(patch.every_seconds)
            )
            updated = WidgetDefinition(
                id=current.id,
                title=title,
                description=description,
                view=view,
                command=command,
                timeout_seconds=timeout,
                every_seconds=every_seconds,
                workspace=current.workspace,
                created_at=current.created_at,
                updated_at=now_iso(),
            )
            if html is not None:
                self._store.write_html(widget_id, html)
            if script is not None:
                self._store.write_script(widget_id, filename, script)
            self._store.write_definition(updated)
            command_changed = patch.command is not None or patch.script is not None
            await self._notify(WidgetChange(widget_id, "updated"))
            result = await self.refresh(widget_id) if command_changed and command else None
            self._schedule(widget_id, every_seconds if command else None)
            return updated, result

    async def delete(self, widget_id: str) -> None:
        async with self._definition_lock:
            self._store.remove(widget_id)
            self._next_runs.pop(widget_id, None)
            self._run_locks.pop(widget_id, None)
            await self._notify(WidgetChange(widget_id, "deleted"))

    async def set_data(self, widget_id: str, data: object) -> WidgetDefinition:
        definition = self._store.read(widget_id)
        validated = validate_data(definition.view, data)
        async with self._run_lock(widget_id):
            self._store.write_data(widget_id, validated)
        await self._notify(WidgetChange(widget_id, "data"))
        return definition

    async def refresh(self, widget_id: str) -> WidgetRunResult:
        definition = self._store.read(widget_id)
        if not definition.command:
            return WidgetRunResult(error="widget has no command")
        async with self._run_lock(widget_id):
            result = await self._runner.run(
                definition,
                widget_dir=self._store.directory(widget_id),
                workspace=_workspace_path(definition.workspace),
            )
            envelope = self._store.read_envelope(widget_id)
            previous = envelope[0] if envelope else None
            if result.error is not None:
                self._store.write_data(widget_id, previous, error=result.error)
            else:
                try:
                    validate_data(definition.view, result.data)
                except WidgetError as exc:
                    result = WidgetRunResult(
                        data=result.data, error=str(exc), duration_ms=result.duration_ms,
                    )
                self._store.write_data(widget_id, result.data, error=result.error)
        self._schedule(widget_id, definition.every_seconds)
        await self._notify(WidgetChange(widget_id, "data"))
        return result

    # -- refresh loop ------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        now = now_ms()
        for summary in self.summaries():
            definition = summary.definition
            if definition.every_seconds and definition.command:
                self._next_runs[definition.id] = now + definition.every_seconds * 1000
        self._task = asyncio.create_task(self._tick_loop(), name="yoreni-widget-refresh")

    def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()

    async def aclose(self) -> None:
        task = self._task
        self.stop()
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("widget refresh sweep failed")
            await asyncio.sleep(self._next_delay_ms() / 1000)

    async def _tick(self) -> None:
        due = [widget_id for widget_id, at in self._next_runs.items() if at <= now_ms()]
        for widget_id in due:
            try:
                definition = self._store.read(widget_id)
            except WidgetNotFoundError:
                self._next_runs.pop(widget_id, None)
                continue
            except WidgetError:
                logger.warning("skipping unreadable widget {}", widget_id)
                self._next_runs.pop(widget_id, None)
                continue
            if not definition.command or not definition.every_seconds:
                self._next_runs.pop(widget_id, None)
                continue
            try:
                if self._admission is not None:
                    await self._admission.wait_until_open()
                    with self._admission.activity():
                        await self.refresh(widget_id)
                else:
                    await self.refresh(widget_id)
            except RuntimeMaintenanceError:
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("widget refresh failed for {}", widget_id)
                self._next_runs[widget_id] = now_ms() + definition.every_seconds * 1000

    def _next_delay_ms(self) -> int:
        if not self._next_runs:
            return _TICK_CEILING_MS
        wait = min(self._next_runs.values()) - now_ms()
        return min(max(wait, _TICK_FLOOR_MS), _TICK_CEILING_MS)

    # -- helpers -----------------------------------------------------------

    def _run_lock(self, widget_id: str) -> asyncio.Lock:
        lock = self._run_locks.get(widget_id)
        if lock is None:
            lock = asyncio.Lock()
            self._run_locks[widget_id] = lock
        return lock

    def _script_name(self, definition: WidgetDefinition) -> str | None:
        for arg in reversed(definition.command or ()):
            if WIDGET_FILE_RE.fullmatch(arg) is None:
                continue
            if self._store.read_script(definition.id, arg) is not None:
                return arg
        return None

    def _script_filename(self, definition: WidgetDefinition) -> str:
        """Reuse the script file the current command runs when only its source changes."""
        return self._script_name(definition) or "data.py"

    def _summary(self, definition: WidgetDefinition) -> WidgetSummary:
        envelope = self._store.read_envelope(definition.id)
        updated_at_ms: int | None = None
        error: str | None = None
        if envelope is not None:
            updated_at_ms = envelope[1] or None
            error = envelope[2]
        return WidgetSummary(
            definition=definition,
            updated_at_ms=updated_at_ms,
            error=error,
            has_data=envelope is not None,
            next_run_at_ms=self._next_runs.get(definition.id),
        )

    def _schedule(self, widget_id: str, every_seconds: int | None) -> None:
        if every_seconds:
            self._next_runs[widget_id] = now_ms() + every_seconds * 1000
        else:
            self._next_runs.pop(widget_id, None)

    def _exists(self, widget_id: str) -> bool:
        try:
            self._store.read(widget_id)
        except WidgetNotFoundError:
            return False
        return True

    async def _notify(self, change: WidgetChange) -> None:
        if self._on_change is None:
            return
        try:
            await self._on_change(change)
        except Exception:
            logger.exception("widget change callback failed for {}", change.widget_id)


def _validate_spec(
    spec: WidgetSpec,
) -> tuple[str | None, str | None, str, tuple[str, ...] | None, int, int | None]:
    view = validate_view(spec.view)
    if view == "html":
        if spec.html is None:
            raise WidgetError("html is required when view='html'")
        html = validate_html(spec.html)
    else:
        if spec.html is not None:
            raise WidgetError("html is only allowed when view='html'")
        html = None
    script = validate_script(spec.script) if spec.script is not None else None
    filename = validate_widget_filename(spec.script_filename)
    command = validate_command(spec.command)
    if command is None and script is not None:
        command = ("python3", filename)
    return (
        html,
        script,
        filename,
        command,
        validate_timeout(spec.timeout_seconds),
        validate_refresh_interval(spec.every_seconds),
    )


def _validate_workspace(value: str | None, fallback: Path) -> str:
    candidate = Path(value).expanduser() if value else fallback
    resolved = candidate.resolve(strict=False)
    if not resolved.is_dir():
        raise WidgetError(f"workspace does not exist: {resolved}")
    return str(resolved)


def _workspace_path(value: str | None) -> Path | None:
    return Path(value) if value else None
