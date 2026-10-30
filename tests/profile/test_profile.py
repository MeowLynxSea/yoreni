from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from yoreni.agent.context import ContextBuilder
from yoreni.profile.prompt import render_profile_section
from yoreni.profile.schema import ChatStyle, UserProfile
from yoreni.profile.store import UserProfileStore


def test_profile_section_carries_identity_and_style() -> None:
    profile = UserProfile(
        name="Ada",
        birthday=date(1990, 12, 10),
        gender="female",
        notes="Prefer short answers.",
        style=ChatStyle(base="professional", affability="less", emoji="more"),
    )

    rendered = render_profile_section(profile)

    assert "Preferred name: Ada" in rendered
    assert "Birthday: 1990-12-10" in rendered
    assert "Gender: female" in rendered
    assert "Prefer short answers." in rendered
    assert "accurate, logical, and professional" in rendered
    assert "Keep the tone reserved" in rendered
    assert "Use emoji sparingly" in rendered
    # Neutral dimensions add no instruction.
    assert "more energy" not in rendered
    assert "headings and lists" not in rendered


def test_custom_style_and_empty_profile() -> None:
    custom = UserProfile(
        name="Ada",
        style=ChatStyle(base="custom", custom="Speak like a ship captain."),
    )
    assert "Speak like a ship captain." in render_profile_section(custom)
    assert render_profile_section(UserProfile()) == ""


def test_custom_style_requires_text() -> None:
    profile = UserProfile(name="Ada", style=ChatStyle(base="custom", custom=" "))
    with pytest.raises(ValueError, match="custom style"):
        profile.require_complete()


def test_profile_store_round_trip_and_tolerates_bad_file(tmp_path: Path) -> None:
    store = UserProfileStore(tmp_path / "profile.json")
    assert store.read() == UserProfile()

    saved = UserProfile(name="Ada", birthday=date(1991, 2, 3), notes="Likes tea.")
    store.write(saved)
    assert store.read() == saved

    (tmp_path / "profile.json").write_text("{not json", encoding="utf-8")
    assert store.read() == UserProfile()


def test_context_prompt_includes_user_md_and_profile(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("Long-term summary of Ada.", encoding="utf-8")
    store = UserProfileStore(tmp_path / "profile.json")
    store.write(UserProfile(
        name="Ada",
        notes="Avoid jargon.",
        style=ChatStyle(base="friendly", structure="less"),
    ))

    prompt = ContextBuilder(tmp_path, profile_store=store).build_system_prompt()

    assert "Long-term summary of Ada." in prompt
    assert "Preferred name: Ada" in prompt
    assert "Avoid jargon." in prompt
    assert "Be warm, gentle" in prompt
    assert "Prefer flowing prose" in prompt


def test_context_prompt_without_profile_store_keeps_user_md(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("Only the summary.", encoding="utf-8")

    prompt = ContextBuilder(tmp_path).build_system_prompt()

    assert "Only the summary." in prompt
    assert "Preferred name" not in prompt
