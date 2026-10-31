"""Session support for long-running commands inside the sandbox VM."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext, current_execution_owner
from yoreni.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.agent.tools.shell_config import ExecToolConfig
from yoreni.agent.tools.shell_output import ShellOutputFilter
from yoreni.sandbox.gateway import SandboxGateway, get_sandbox_gateway

DEFAULT_YIELD_MS = 1000
MAX_YIELD_MS = 30_000
DEFAULT_WAIT_FOR_MS = 10_000
DEFAULT_UNTIL_EXIT_MS = 600_000
MAX_WAIT_FOR_MS = 600_000
DEFAULT_MAX_OUTPUT_CHARS = 10_000
MAX_OUTPUT_CHARS = 50_000

FinishReason = Literal[
    "exit", "max_runtime", "idle_timeout", "cancelled", "owner_closed", "shutdown",
    "execution_error",
]


@dataclass(slots=True)
class _SessionPoll:
    output: str
    done: bool
    exit_code: int | None
    elapsed_s: float = 0.0
    finish_reason: FinishReason | None = None
    stopping: bool = False
    output_idle_s: float = 0.0
    idle_timeout: int = 0
    remaining_s: float | None = None
    stdin_closed: bool = False
    truncated_chars: int = 0
    output_incomplete: bool = False
    execution_error: str | None = None

    @property
    def failed(self) -> bool:
        return self.timed_out or self.finish_reason == "execution_error"

    @property
    def timed_out(self) -> bool:
        return self.finish_reason in ("max_runtime", "idle_timeout")

    @property
    def terminated(self) -> bool:
        return self.finish_reason in ("cancelled", "owner_closed", "shutdown")


@dataclass(slots=True)
class ExecSessionInfo:
    session_id: str
    command: str
    cwd: str
    elapsed_s: float
    idle_s: float
    remaining_s: float | None
    returncode: int | None
    owner_id: str | None = None
    idle_timeout: int = 0
    stopping: bool = False
    finish_reason: FinishReason | None = None
    cleanup_error: str | None = None


@dataclass
class _TrackedSession:
    id: str
    command: str
    cwd: str
    owner_id: str | None
    started_at: float
    idle_timeout: int
    last_output_at: float
    timeout_s: float | None = None
    offset: int = 0
    finished_at: float | None = None
    finish_reason: FinishReason | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ExecSessionManager:
    """Gateway-backed sessions preserving the tool's public contract."""

    def __init__(self, gateway: SandboxGateway | None = None) -> None:
        self._gateway = gateway
        self._sessions: dict[str, _TrackedSession] = {}
        self._filters: dict[str, ShellOutputFilter] = {}

    def _gw(self) -> SandboxGateway:
        return self._gateway or get_sandbox_gateway()

    @property
    def has_pending_work(self) -> bool:
        return any(session.finished_at is None for session in self._sessions.values())

    async def start(
        self,
        *,
        command: str | list[str],
        cwd: str,
        env: dict[str, str],
        timeout: int | None,
        shell_program: str | None,
        login: bool,
        yield_time_ms: int,
        until_exit: bool,
        stdin: Any = None,
        idle_timeout: int = 300,
        idle_action: str = "report",
        owner_id: str | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> tuple[str, _SessionPoll]:
        del env, shell_program, stdin
        gateway = self._gw()
        process_id = await gateway.start_process(
            command,
            cwd=cwd,
            timeout_s=float(timeout) if timeout else None,
            login=login,
            owner_id=owner_id,
        )
        tracked = _TrackedSession(
            id=process_id,
            command=command if isinstance(command, str) else " ".join(map(str, command)),
            cwd=cwd,
            owner_id=owner_id,
            started_at=time.monotonic(),
            idle_timeout=idle_timeout,
            last_output_at=time.monotonic(),
            timeout_s=float(timeout) if timeout else None,
        )
        tracked.extra["max_output_chars"] = max_output_chars
        tracked.extra["idle_action"] = idle_action
        self._sessions[process_id] = tracked
        self._filters[process_id] = ShellOutputFilter()
        if timeout:
            asyncio.create_task(self._enforce_timeout(process_id, float(timeout)))
        if until_exit:
            return process_id, await self._wait(process_id, timeout_ms=None)
        if yield_time_ms > 0:
            await asyncio.sleep(min(yield_time_ms, MAX_YIELD_MS) / 1000)
        return process_id, self._poll(process_id, max_output_chars=max_output_chars)

    async def _enforce_timeout(self, session_id: str, timeout_s: float) -> None:
        await asyncio.sleep(timeout_s)
        tracked = self._sessions.get(session_id)
        if tracked is None or tracked.finished_at is not None:
            return
        await self._gw().kill_process(session_id, reason="max_runtime")
        tracked.finish_reason = "max_runtime"
        tracked.finished_at = time.monotonic()

    async def write(
        self,
        *,
        session_id: str,
        chars: str | None,
        close_stdin: bool,
        terminate: bool,
        yield_time_ms: int,
        max_output_chars: int = MAX_OUTPUT_CHARS,
        owner_id: str | None = None,
    ) -> _SessionPoll:
        tracked = self._sessions.get(session_id)
        if tracked is None:
            raise KeyError(session_id)
        if owner_id is not None and tracked.owner_id not in (None, owner_id):
            raise PermissionError("exec session belongs to another run")
        gateway = self._gw()
        if terminate:
            await gateway.kill_process(session_id, reason="cancelled")
            tracked.finish_reason = "cancelled"
            tracked.finished_at = time.monotonic()
            return self._poll(session_id, max_output_chars=max_output_chars)
        if chars is not None:
            await gateway.write_process(session_id, chars)
        if close_stdin:
            await gateway.close_process_stdin(session_id)
        if yield_time_ms > 0:
            await asyncio.sleep(min(yield_time_ms, MAX_YIELD_MS) / 1000)
        return self._poll(session_id, max_output_chars=max_output_chars)

    async def _wait(
        self,
        session_id: str,
        *,
        timeout_ms: int | None,
        max_output_chars: int = MAX_OUTPUT_CHARS,
    ) -> _SessionPoll:
        deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000
        while True:
            poll = self._poll(session_id, max_output_chars=max_output_chars)
            if poll.done:
                return poll
            if deadline is not None and time.monotonic() >= deadline:
                return poll
            await asyncio.sleep(0.2 if deadline is None else min(0.2, max(0.01, deadline - time.monotonic())))

    def _poll(self, session_id: str, *, max_output_chars: int = MAX_OUTPUT_CHARS) -> _SessionPoll:
        tracked = self._sessions[session_id]
        gateway = self._gw()
        raw = gateway.poll_process(session_id, offset=tracked.offset)
        tracked.offset += len(raw.output)
        now = time.monotonic()
        if raw.output:
            tracked.last_output_at = now
        filtered = self._filters[session_id].feed(raw.output)
        output = filtered
        truncated = raw.truncated_chars
        if len(output) > max_output_chars:
            dropped = len(output) - max_output_chars
            truncated += dropped
            half = max_output_chars // 2
            output = (
                output[:half]
                + f"\n... ({dropped:,} chars truncated) ...\n"
                + output[-(max_output_chars - half):]
            )
        idle = now - tracked.last_output_at
        if raw.done:
            tracked.finished_at = tracked.finished_at or now
            tracked.finish_reason = tracked.finish_reason or _reason(raw.finish_reason)
        elif tracked.idle_timeout and idle >= tracked.idle_timeout:
            if tracked.extra.get("idle_action") == "terminate":
                asyncio.create_task(gateway.kill_process(session_id, reason="idle_timeout"))
                tracked.finish_reason = "idle_timeout"
        remaining = None
        if tracked.idle_timeout:
            remaining = max(0.0, tracked.idle_timeout - idle)
        return _SessionPoll(
            output=output,
            done=raw.done,
            exit_code=raw.exit_code,
            elapsed_s=now - tracked.started_at,
            finish_reason=tracked.finish_reason,
            stopping=tracked.finish_reason in {"cancelled", "owner_closed", "shutdown"},
            output_idle_s=idle,
            idle_timeout=tracked.idle_timeout,
            remaining_s=remaining,
            truncated_chars=truncated,
        )

    async def list(self, *, owner_id: str | None = None) -> list[ExecSessionInfo]:
        infos: list[ExecSessionInfo] = []
        now = time.monotonic()
        for session_id, tracked in self._sessions.items():
            if owner_id is not None and tracked.owner_id not in (None, owner_id):
                continue
            poll = self._poll(session_id)
            infos.append(
                ExecSessionInfo(
                    session_id=session_id,
                    command=tracked.command,
                    cwd=tracked.cwd,
                    elapsed_s=now - tracked.started_at,
                    idle_s=now - tracked.last_output_at,
                    remaining_s=poll.remaining_s,
                    returncode=poll.exit_code,
                    owner_id=tracked.owner_id,
                    idle_timeout=tracked.idle_timeout,
                    stopping=poll.stopping,
                    finish_reason=poll.finish_reason,
                )
            )
        return infos

    async def close_all(self) -> int:
        count = 0
        for session_id, tracked in list(self._sessions.items()):
            if tracked.finished_at is None:
                await self._gw().kill_process(session_id, reason="shutdown")
                count += 1
        self._sessions.clear()
        self._filters.clear()
        return count

    async def terminate_by_owner(self, owner_id: str) -> int:
        count = 0
        for session_id, tracked in list(self._sessions.items()):
            if tracked.owner_id == owner_id and tracked.finished_at is None:
                if await self._gw().kill_process(session_id, reason="owner_closed"):
                    tracked.finish_reason = "owner_closed"
                    tracked.finished_at = time.monotonic()
                    count += 1
        return count


DEFAULT_EXEC_SESSION_MANAGER = ExecSessionManager()


def _reason(value: str | None) -> FinishReason | None:
    allowed = {
        "exit", "max_runtime", "idle_timeout", "cancelled", "owner_closed",
        "shutdown", "execution_error",
    }
    if value in allowed:
        return value  # type: ignore[return-value]
    return "exit" if value else None


def clamp_session_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    return max(minimum, min(maximum, int(value)))


def format_session_poll(session_id: str, poll: _SessionPoll) -> str:
    parts = [poll.output] if poll.output else []
    if poll.truncated_chars:
        parts.append(f"({poll.truncated_chars:,} chars truncated from output)")
    if poll.finish_reason == "max_runtime":
        parts.append("Error: Command timed out; session was terminated. Reason: max_runtime.")
    elif poll.finish_reason == "idle_timeout":
        parts.append(
            "Error: Output silence limit reached; session was terminated. "
            "Reason: idle_timeout."
        )
    elif poll.terminated:
        parts.append(f"Session terminated. Reason: {poll.finish_reason}.")
    elif poll.finish_reason == "execution_error":
        parts.append(
            f"Error: Command output failed: {poll.execution_error}. "
            "Reason: execution_error."
        )
    if poll.output_incomplete:
        parts.append("Output collection ended before EOF; output may be incomplete.")
    if poll.stdin_closed:
        parts.append("Stdin closed.")
    if poll.done:
        parts.append(f"Exit code: {poll.exit_code}")
    else:
        state = "stopping" if poll.stopping else "running"
        parts.append(f"Process {state}. session_id: {session_id}")
        if poll.idle_timeout and poll.output_idle_s >= poll.idle_timeout:
            parts.append(
                f"Output silent for {poll.output_idle_s:.1f}s; "
                "no stdout/stderr bytes observed. This does not establish a stall."
            )
        remaining = "unlimited" if poll.remaining_s is None else f"{poll.remaining_s:.1f}s"
        parts.append(f"Runtime remaining: {remaining}")
    parts.append(f"Elapsed: {poll.elapsed_s:.1f}s")
    return "\n".join(parts) if parts else "(no output yet)"


@tool_parameters(
    tool_parameters_schema(
        session_id=StringSchema("Session ID returned by exec"),
        input=StringSchema("Text to write to the process stdin", nullable=True),
        close_stdin=BooleanSchema(description="Close stdin after writing", default=False),
        terminate=BooleanSchema(description="Terminate the session", default=False),
        wait_for=StringSchema(
            "Return when this text appears in output.", min_length=1, nullable=True,
        ),
        until_exit=BooleanSchema(
            description=(
                "Wait for exit within timeout_ms; return the active session if the "
                "wait expires."
            ),
            default=False,
        ),
        timeout_ms=IntegerSchema(
            description=(
                "Maximum wait: 1s normally, 10s for wait_for, 10m for until_exit."
            ),
            minimum=0,
            maximum=MAX_WAIT_FOR_MS,
            nullable=True,
        ),
        required=["session_id"],
    )
)
class ExecSessionTool(Tool):
    """Interact with or wait for a running exec session."""

    interruptible = True
    _scopes = {"core", "subagent"}
    config_key = "exec"

    @classmethod
    def config_cls(cls):
        return ExecToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.exec.enable

    def __init__(self, *, manager: ExecSessionManager | None = None) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(manager=ctx.exec_session_manager)

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "exec_session"

    @property
    def description(self) -> str:
        return "Manage a session returned by exec."

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        session_id: str,
        input: str | None = None,
        close_stdin: bool = False,
        terminate: bool = False,
        wait_for: str | None = None,
        until_exit: bool = False,
        timeout_ms: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if wait_for == "":
                return ToolResult.error("Error: wait_for must not be empty.")
            if wait_for is not None and until_exit:
                return ToolResult.error(
                    "Error: wait_for and until_exit are mutually exclusive."
                )
            if terminate and (
                input is not None or close_stdin or wait_for is not None
                or until_exit or timeout_ms is not None
            ):
                return ToolResult.error("Error: terminate must be used alone.")
            if terminate:
                poll = await self._manager.write(
                    session_id=session_id,
                    chars=None,
                    close_stdin=False,
                    terminate=True,
                    yield_time_ms=0,
                )
                result = format_session_poll(session_id, poll)
                return ToolResult.error(result) if poll.failed else result
            if input is not None or close_stdin:
                await self._manager.write(
                    session_id=session_id,
                    chars=input,
                    close_stdin=close_stdin,
                    terminate=False,
                    yield_time_ms=0,
                    owner_id=current_execution_owner(),
                )
            if until_exit or wait_for is not None or timeout_ms is not None:
                timeout = timeout_ms
                if timeout is None:
                    timeout = DEFAULT_UNTIL_EXIT_MS if until_exit else DEFAULT_WAIT_FOR_MS
                poll = await self._wait_for(
                    session_id,
                    wait_for=wait_for,
                    timeout_ms=clamp_session_int(timeout, DEFAULT_YIELD_MS, 0, MAX_WAIT_FOR_MS),
                )
                result = format_session_poll(session_id, poll)
                return ToolResult.error(result) if poll.failed else result
            poll = await self._manager.write(
                session_id=session_id,
                chars=None,
                close_stdin=False,
                terminate=False,
                yield_time_ms=DEFAULT_YIELD_MS,
                owner_id=current_execution_owner(),
            )
            result = format_session_poll(session_id, poll)
            return ToolResult.error(result) if poll.failed else result
        except KeyError:
            return ToolResult.error(f"Error: exec session not found: {session_id!r}")
        except PermissionError as exc:
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:
            return ToolResult.error(f"Error managing exec session: {exc}")

    async def _wait_for(
        self,
        session_id: str,
        *,
        wait_for: str | None,
        timeout_ms: int,
    ) -> _SessionPoll:
        deadline = time.monotonic() + timeout_ms / 1000
        overlap = ""
        while True:
            poll = await self._manager.write(
                session_id=session_id,
                chars=None,
                close_stdin=False,
                terminate=False,
                yield_time_ms=0,
            )
            if wait_for is not None and poll.output:
                searchable = overlap + poll.output
                if wait_for in searchable:
                    poll.output += f"\nWait target observed: {wait_for!r}"
                    return poll
                keep = max(0, len(wait_for) - 1)
                overlap = searchable[-keep:] if keep else ""
            if poll.done or time.monotonic() >= deadline:
                if not poll.done and wait_for is not None:
                    poll.output += f"\nWait target not observed: {wait_for!r}"
                return poll
            await asyncio.sleep(0.2)


@tool_parameters(tool_parameters_schema())
class ListExecSessionsTool(Tool):
    """List active exec sessions."""

    _scopes = {"core", "subagent"}
    config_key = "exec"

    @classmethod
    def config_cls(cls):
        return ExecToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.exec.enable

    def __init__(self, *, manager: ExecSessionManager | None = None) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(manager=ctx.exec_session_manager)

    @property
    def name(self) -> str:
        return "list_exec_sessions"

    @property
    def description(self) -> str:
        return "List active exec sessions."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        try:
            sessions = await self._manager.list(owner_id=current_execution_owner())
            if not sessions:
                return "No active exec sessions."
            lines: list[str] = []
            for info in sessions:
                command = " ".join(info.command.split())
                if len(command) > 120:
                    command = command[:119] + "..."
                status = "finished" if info.finish_reason is not None else "running"
                lines.append(
                    f"{info.session_id} | {status} | elapsed={info.elapsed_s:.1f}s "
                    f"| idle={info.idle_s:.1f}s | exit={info.returncode} "
                    f"| reason={info.finish_reason or '-'} | cwd={info.cwd} | {command}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return ToolResult.error(f"Error listing exec sessions: {exc}")
