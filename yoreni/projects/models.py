"""Persisted project records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yoreni.projects.scope import project_scope


@dataclass(frozen=True, slots=True)
class Project:
    """One long-running workspace owned by the single personal agent."""

    id: str
    name: str
    description: str
    emoji: str
    created_at_ms: int
    updated_at_ms: int

    @property
    def scope(self) -> str:
        return project_scope(self.id)

    @property
    def link(self) -> str:
        """WebUI path that opens this project directly."""
        return f"/#/projects/{self.id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "emoji": self.emoji,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "link": self.link,
        }


@dataclass(frozen=True, slots=True)
class ProjectHandoff:
    """Work handed from one conversation to a project, with its sources.

    A handoff carries the brief and references to the messages it came from; it
    never copies the source conversation into the project.
    """

    id: str
    project_id: str
    brief: str
    source_refs: tuple[str, ...]
    created_at_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "brief": self.brief,
            "source_refs": list(self.source_refs),
            "created_at_ms": self.created_at_ms,
        }
