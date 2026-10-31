"""Transport-neutral values owned by the execution plane."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class ControlOwner(StrEnum):
    """Who may send input to the VM desktop and drive browser automation."""

    AGENT = "agent"
    PAUSING = "pausing"
    USER = "user"


@dataclass(frozen=True)
class ControlSnapshot:
    owner: ControlOwner
    epoch: int
    controller_id: str | None
    since_ms: int
    reason: str | None = None

    @property
    def user_controls_desktop(self) -> bool:
        return self.owner is ControlOwner.USER

    def as_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner.value,
            "epoch": self.epoch,
            "controller": self.controller_id,
            "since_ms": self.since_ms,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExecResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float = 0.0
    timed_out: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class FileInfo:
    path: str
    is_dir: bool
    is_file: bool
    is_symlink: bool
    size: int
    mode: int
    mtime_s: float


@dataclass(frozen=True)
class SandboxHealth:
    running: bool
    desktop_ready: bool
    browser_ready: bool
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "desktop_ready": self.desktop_ready,
            "browser_ready": self.browser_ready,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SandboxStatus:
    backend: str
    box_id: str | None
    name: str
    state: Literal["created", "starting", "running", "stopped", "failed", "unknown"]
    workspace: str
    health: SandboxHealth
    started_at_ms: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "box_id": self.box_id,
            "name": self.name,
            "state": self.state,
            "workspace": self.workspace,
            "health": self.health.as_dict(),
            "started_at_ms": self.started_at_ms,
            "detail": self.detail,
        }


@dataclass
class ProcessHandle:
    """One live process started by the gateway."""

    id: str
    command: str
    cwd: str
    started_at: float = field(default_factory=time.monotonic)
    exit_code: int | None = None
    finish_reason: str | None = None
