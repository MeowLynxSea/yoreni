"""The signing-in skill is the agent's reference for the authentication flow."""

from yoreni.agent.skill_metadata import parse_skill_metadata, valid_skill_metadata
from yoreni.agent.skills import BUILTIN_SKILLS_DIR

_SKILL = BUILTIN_SKILLS_DIR / "signing-in" / "SKILL.md"


def test_signing_in_skill_identity_matches_its_directory() -> None:
    metadata = parse_skill_metadata(_SKILL.read_text(encoding="utf-8"))

    assert metadata is not None
    assert valid_skill_metadata(metadata, "signing-in")
