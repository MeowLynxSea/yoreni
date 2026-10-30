"""The Home widget skill is the agent's reference for the widget tool."""

from typing import Any, cast

from yoreni.agent.skill_metadata import parse_skill_metadata, valid_skill_metadata
from yoreni.agent.skills import BUILTIN_SKILLS_DIR
from yoreni.agent.tools.widget import _WIDGET_PARAMETERS

_SKILL = BUILTIN_SKILLS_DIR / "home-widgets" / "SKILL.md"


def _enum(parameter: str) -> list[str]:
    properties = cast(dict[str, Any], _WIDGET_PARAMETERS["properties"])
    return cast(list[str], cast(dict[str, Any], properties[parameter])["enum"])


def test_home_widgets_skill_identity_matches_its_directory() -> None:
    metadata = parse_skill_metadata(_SKILL.read_text(encoding="utf-8"))

    assert metadata is not None
    assert valid_skill_metadata(metadata, "home-widgets")


def test_home_widgets_skill_documents_every_action_and_view() -> None:
    content = _SKILL.read_text(encoding="utf-8")

    for action in _enum("action"):
        assert f'action="{action}"' in content
    for view in _enum("view"):
        assert f'view="{view}"' in content
