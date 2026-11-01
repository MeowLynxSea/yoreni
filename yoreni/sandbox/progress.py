"""First-run progress for the sandbox VM.

The setup stages are ordered and weighted so one percentage covers the whole
first start: creating the VM (image layers), installing system packages,
preparing the browser, and starting the desktop services. Stages without a byte
signal report no percentage, and the caller renders them as indeterminate.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

SetupState = Literal["idle", "running", "ready", "failed", "unsupported"]
SetupStage = Literal[
    "starting", "backup", "image", "configuring", "packages", "browser", "restore",
    "services", "ready",
]

# Percent range each stage occupies. `ready` closes the bar.
_STAGE_SPAN: dict[SetupStage, tuple[float, float]] = {
    "starting": (0.0, 3.0),
    # Only a rebuild backs the workspace up first; a first start skips ahead.
    "backup": (0.0, 3.0),
    "image": (3.0, 58.0),
    # Extraction has no byte signal and must not move the bar.
    "configuring": (58.0, 58.0),
    "packages": (58.0, 74.0),
    "browser": (74.0, 90.0),
    "restore": (90.0, 96.0),
    "services": (96.0, 100.0),
    "ready": (100.0, 100.0),
}

_STAGE_LABEL: dict[SetupStage, str] = {
    "starting": "Preparing the sandbox",
    "backup": "Backing up the sandbox files",
    "image": "Downloading the system image",
    "configuring": "Configuring the sandbox",
    "packages": "Installing system packages",
    "browser": "Preparing the browser",
    "restore": "Restoring the sandbox files",
    "services": "Starting the desktop",
    "ready": "Sandbox is ready",
}


class SandboxProgress:
    """Thread-safe monotonic progress for one first-start sequence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: SetupState = "idle"
        self._stage: SetupStage = "starting"
        self._percent: float | None = None
        self._detail = ""
        self._error = ""
        self._downloaded_bytes: int | None = None
        self._total_bytes: int | None = None
        self._rate_bytes_per_s: float | None = None
        self._eta_seconds: int | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._last_bytes_at: float | None = None
        self._last_bytes: int = 0

    # -- writes --------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            self._state = "running"
            self._stage = "starting"
            self._percent = 0.0
            self._detail = _STAGE_LABEL["starting"]
            self._error = ""
            self._downloaded_bytes = None
            self._total_bytes = None
            self._rate_bytes_per_s = None
            self._eta_seconds = None
            self._started_at = time.monotonic()
            self._finished_at = None
            self._last_bytes_at = None
            self._last_bytes = 0

    def set_stage(self, stage: SetupStage, *, detail: str = "") -> None:
        with self._lock:
            if self._state != "running":
                return
            self._stage = stage
            self._detail = detail or _STAGE_LABEL[stage]
            self._eta_seconds = None
            self._rate_bytes_per_s = None
            self._last_bytes_at = None
            self._last_bytes = 0
            # A stage without a byte signal must render indeterminate: the
            # previous stage's totals would otherwise freeze its bar.
            self._downloaded_bytes = None
            self._total_bytes = None
            if stage == "ready":
                self._percent = 100.0
            elif self._percent is None or self._percent < _STAGE_SPAN[stage][0]:
                self._percent = _STAGE_SPAN[stage][0]

    def set_bytes(self, downloaded: int, total: int | None) -> None:
        """Report byte progress inside the current stage."""
        with self._lock:
            if self._state != "running":
                return
            now = time.monotonic()
            if self._last_bytes_at is not None and downloaded > self._last_bytes:
                elapsed = now - self._last_bytes_at
                if elapsed >= 0.2:
                    instant = (downloaded - self._last_bytes) / elapsed
                    previous = self._rate_bytes_per_s or instant
                    self._rate_bytes_per_s = previous * 0.6 + instant * 0.4
                    self._last_bytes_at = now
                    self._last_bytes = downloaded
            else:
                self._last_bytes_at = now
                self._last_bytes = downloaded
            if total and total > 0:
                # A poll can read a partially written file as larger than the
                # source archive; never report more than the image itself.
                downloaded = min(downloaded, total)
            self._downloaded_bytes = downloaded
            self._total_bytes = total
            if not total or total <= 0:
                # Bytes are moving but the size is unknown, so the bar has no
                # position to show: stay indeterminate instead of freezing on
                # the stage floor while the transfer runs.
                low, _ = _STAGE_SPAN[self._stage]
                if self._percent is None or self._percent <= low:
                    self._percent = None
                self._eta_seconds = None
                return
            fraction = min(1.0, max(0.0, downloaded / total))
            low, high = _STAGE_SPAN[self._stage]
            value = low + (high - low) * fraction
            if self._percent is None or value > self._percent:
                self._percent = value
            if fraction >= 1:
                # The transfer is complete; extraction has no byte signal.
                self._rate_bytes_per_s = None
                self._eta_seconds = None
                return
            rate = self._rate_bytes_per_s
            if rate and rate > 0:
                self._eta_seconds = int(max(0, (total - downloaded) / rate))
            else:
                self._eta_seconds = None

    def finish(self) -> None:
        with self._lock:
            self._state = "ready"
            self._stage = "ready"
            self._percent = 100.0
            self._detail = _STAGE_LABEL["ready"]
            self._eta_seconds = 0
            self._finished_at = time.monotonic()

    def fail(self, message: str) -> None:
        with self._lock:
            self._state = "failed"
            self._error = message.strip()[:800]
            self._detail = self._error
            self._eta_seconds = None
            self._finished_at = time.monotonic()

    def mark_unsupported(self, detail: str) -> None:
        with self._lock:
            self._state = "unsupported"
            self._detail = detail
            self._percent = 100.0

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._finished_at or time.monotonic()
            elapsed = 0.0 if self._started_at is None else max(0.0, now - self._started_at)
            return {
                "state": self._state,
                "stage": self._stage,
                "percent": None if self._percent is None else round(self._percent, 1),
                "detail": self._detail,
                "error": self._error,
                "downloaded_bytes": self._downloaded_bytes,
                "total_bytes": self._total_bytes,
                "rate_bytes_per_s": None
                if self._rate_bytes_per_s is None
                else round(self._rate_bytes_per_s, 0),
                "eta_seconds": self._eta_seconds,
                "elapsed_s": round(elapsed, 1),
            }
