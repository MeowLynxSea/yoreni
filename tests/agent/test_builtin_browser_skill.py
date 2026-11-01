import re

from yoreni.agent.skill_metadata import (
    BUILTIN_SKILLS_DIR,
    parse_skill_metadata,
    valid_skill_metadata,
)

SKILL = BUILTIN_SKILLS_DIR / "browser-skill"


def test_browser_skill_has_valid_identity() -> None:
    content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    metadata = parse_skill_metadata(content)

    assert metadata is not None
    assert valid_skill_metadata(metadata, "browser-skill")


def test_browser_skill_references_all_exist() -> None:
    content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"references/([a-z-]+\.md)", content))

    assert referenced
    for name in referenced:
        assert (SKILL / "references" / name).is_file(), name
    assert {path.name for path in (SKILL / "references").glob("*.md")} == referenced


def test_browser_skill_describes_the_sandbox_runtime() -> None:
    content = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert "## Yoreni environment" in content
    assert "never start a second one" in content
    assert "no borrowed user tabs" in content
