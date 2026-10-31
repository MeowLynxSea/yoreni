"""Durable storage for the user profile and onboarding progress."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yoreni.config.paths import get_config_path, get_data_dir
from yoreni.profile.schema import UserProfile
from yoreni.utils.file_io import write_text_atomic

if TYPE_CHECKING:
    from yoreni.config.schema import Config

PROFILE_FILENAME = "profile.json"
ONBOARDING_FILENAME = "onboarding.json"
_PROFILE_VERSION = 1
_ONBOARDING_VERSION = 1

OnboardingStep = Literal[
    "token",
    "language",
    "intro",
    "name",
    "birthday",
    "gender",
    "style",
    "notes",
    "model",
    "embedding",
    "done",
]
ONBOARDING_STEPS: tuple[OnboardingStep, ...] = (
    "token",
    "language",
    "intro",
    "name",
    "birthday",
    "gender",
    "style",
    "notes",
    "model",
    "embedding",
    "done",
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_path(filename: str) -> Path:
    return get_data_dir() / filename


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    with suppress(OSError):
        path.chmod(0o600)


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


class UserProfileStore:
    """Read and replace the owner profile in one durable JSON document."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path(PROFILE_FILENAME)

    def read(self) -> UserProfile:
        data = _read_object(self.path)
        if data is None:
            return UserProfile()
        try:
            return UserProfile.model_validate(data)
        except ValidationError:
            # An unreadable profile must not block startup; the owner can
            # rewrite it from settings or onboarding.
            return UserProfile()

    def write(self, profile: UserProfile) -> UserProfile:
        _write_private(self.path, {"version": _PROFILE_VERSION, **profile.model_dump(mode="json")})
        return profile


def profile_store_for_config(config: "Config") -> UserProfileStore:
    """Locate the profile beside the instance config, not inside a project."""
    data_dir = config.runtime_data_dir
    return UserProfileStore(data_dir / PROFILE_FILENAME if data_dir is not None else None)


def operator_state_files() -> tuple[Path, ...]:
    """Instance files that only the operator or the gateway may write."""
    config_path = get_config_path().expanduser().resolve(strict=False)
    data_dir = config_path.parent
    return (
        config_path,
        *(data_dir / name for name in (PROFILE_FILENAME, ONBOARDING_FILENAME)),
    )


class OnboardingRecord(BaseModel):
    """The durable progress of the first-run flow."""

    model_config = ConfigDict(extra="ignore")

    version: int = _ONBOARDING_VERSION
    status: Literal["in_progress", "completed"] = "in_progress"
    step: OnboardingStep = "token"
    updated_at: str = Field(default_factory=_now_iso)
    completed_at: str | None = None


class OnboardingStore:
    """Persist which step the owner reached and whether setup finished."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path(ONBOARDING_FILENAME)

    def read(self) -> OnboardingRecord | None:
        data = _read_object(self.path)
        if data is None:
            return None
        try:
            return OnboardingRecord.model_validate(data)
        except ValidationError:
            return None

    def write(self, record: OnboardingRecord) -> OnboardingRecord:
        _write_private(self.path, record.model_dump(mode="json"))
        return record

    def start(self) -> OnboardingRecord:
        existing = self.read()
        if existing is not None:
            return existing
        return self.write(OnboardingRecord(status="in_progress", step="token"))

    def mark_step(self, step: OnboardingStep) -> OnboardingRecord:
        record = self.read() or OnboardingRecord()
        record.step = step
        record.status = "in_progress"
        record.updated_at = _now_iso()
        return self.write(record)

    def mark_completed(self) -> OnboardingRecord:
        record = self.read() or OnboardingRecord()
        record.status = "completed"
        record.step = "done"
        record.updated_at = _now_iso()
        record.completed_at = record.completed_at or _now_iso()
        return self.write(record)
