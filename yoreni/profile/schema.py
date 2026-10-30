"""Validated shapes for the structured user profile."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Gender = Literal["male", "female", "undisclosed"]
BaseStyle = Literal[
    "default",
    "professional",
    "friendly",
    "direct",
    "imaginative",
    "pragmatic",
    "custom",
]
Tuning = Literal["more", "neutral", "less"]

CHAT_STYLE_BASES: tuple[BaseStyle, ...] = (
    "default",
    "professional",
    "friendly",
    "direct",
    "imaginative",
    "pragmatic",
    "custom",
)
STYLE_TUNING_VALUES: tuple[Tuning, ...] = ("more", "neutral", "less")

_NAME_MAX = 80
_NOTES_MAX = 2_000
_CUSTOM_STYLE_MAX = 1_000


class ChatStyle(BaseModel):
    """How Yoreni should sound, independent of what it can do."""

    model_config = ConfigDict(extra="ignore")

    base: BaseStyle = "default"
    custom: str = Field(default="", max_length=_CUSTOM_STYLE_MAX)
    # Extra warmth and closeness on top of the base style.
    affability: Tuning = "neutral"
    # Energy and encouragement.
    enthusiasm: Tuning = "neutral"
    # Emoji use.
    emoji: Tuning = "neutral"
    # Headings and bullet lists.
    structure: Tuning = "neutral"

    @field_validator("custom")
    @classmethod
    def _strip_custom(cls, value: str) -> str:
        return value.strip()

    def require_complete(self) -> None:
        """Reject a customized style that has no instruction to follow."""
        if self.base == "custom" and not self.custom:
            raise ValueError("custom style instruction is required when base is custom")


class UserProfile(BaseModel):
    """The owner's identity and preferences as collected at setup."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="", max_length=_NAME_MAX)
    birthday: date | None = None
    gender: Gender | None = None
    style: ChatStyle = Field(default_factory=ChatStyle)
    notes: str = Field(default="", max_length=_NOTES_MAX)

    @field_validator("name", "notes")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("birthday")
    @classmethod
    def _reject_future_birthday(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("birthday cannot be in the future")
        return value

    def require_complete(self) -> None:
        """Reject a profile that cannot drive onboarding completion."""
        if not self.name:
            raise ValueError("name is required")
        self.style.require_complete()


OnboardingStatus = Literal["uninitialized", "in_progress", "completed"]
