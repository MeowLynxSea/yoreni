"""Project tools: create, rename, inspect, hand off and delete projects.

These call the same ProjectService the WebUI uses, so a request made in
conversation produces the same durable record, directory and scoped memory as
one made in the interface.
"""

# pyright: reportIncompatibleMethodOverride=false
from __future__ import annotations

import json
from typing import Any

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ContextAware, RequestContext, ToolContext
from yoreni.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.projects.models import Project
from yoreni.projects.scope import GLOBAL_SCOPE
from yoreni.projects.service import ProjectError, ProjectService


class _ProjectTool(Tool, ContextAware):
    """Shared plumbing: the project service and the current conversation scope."""

    def __init__(self, projects: ProjectService) -> None:
        self._projects = projects
        self._context: RequestContext | None = None

    def set_context(self, ctx: RequestContext) -> None:
        self._context = ctx

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.projects is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.projects is None:
            raise RuntimeError(f"{cls.__name__} requires a ProjectService")
        return cls(ctx.projects)

    def _scope(self) -> str:
        if self._context is None:
            return GLOBAL_SCOPE
        scope = self._context.attributes.get("memory_scope")
        return scope if isinstance(scope, str) and scope else GLOBAL_SCOPE

    def _resolve(self, query: str) -> Project:
        matches = self._projects.find(query)
        if not matches:
            raise ProjectError(f"no project matches {query!r}")
        if len(matches) > 1:
            names = ", ".join(f"{item.name} ({item.id})" for item in matches[:6])
            raise ProjectError(
                f"{query!r} matches several projects: {names}. Ask which one was meant."
            )
        return matches[0]

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)


@tool_parameters(tool_parameters_schema(
    query=StringSchema("Optional text to filter project names.", nullable=True),
))
class ProjectListTool(_ProjectTool):
    """List the projects this agent works in."""

    @property
    def name(self) -> str:
        return "project_list"

    @property
    def description(self) -> str:
        return (
            "List the user's projects with their IDs, descriptions and links. Use it "
            "before answering questions about what the user is working on, or to find "
            "the project that owns a piece of work."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, query: str | None = None, **kwargs: Any) -> str:
        projects = self._projects.find(query) if query else self._projects.list()
        return self._json({
            "projects": [item.as_dict() for item in projects],
            "current_scope": self._scope(),
        })


@tool_parameters(ObjectSchema(
    properties={
        "name": StringSchema("Project name, for example 'Thesis'.", max_length=80),
        "description": StringSchema(
            "Optional one-paragraph description of the work this project holds.",
            nullable=True,
            max_length=2_000,
        ),
        "emoji": StringSchema(
            "Optional emoji shown beside the project name, for example '📄'.",
            nullable=True,
            max_length=8,
        ),
    },
    required=["name"],
    additional_properties=False,
).to_json_schema())
class ProjectCreateTool(_ProjectTool):
    """Create a project with its own directory."""

    @property
    def name(self) -> str:
        return "project_create"

    @property
    def description(self) -> str:
        return (
            "Create a project: an independent workspace with its own conversation, "
            "files and long-term knowledge. Use it when the user starts a body of work "
            "that will continue over time. Returns the link to open it."
        )

    async def execute(
        self, name: str, description: str | None = None,
        emoji: str | None = None, **kwargs: Any,
    ) -> str:
        try:
            project = self._projects.create(
                name=name, description=description or "", emoji=emoji or "",
            )
        except ProjectError as exc:
            return ToolResult.error(f"Error: {exc}")
        return self._json({"project": project.as_dict(), "status": "created"})


@tool_parameters(ObjectSchema(
    properties={
        "project": StringSchema("Project name or ID."),
        "name": StringSchema("New project name.", nullable=True, max_length=80),
        "description": StringSchema("New description.", nullable=True, max_length=2_000),
        "emoji": StringSchema(
            "New emoji shown beside the project name, or an empty string to clear it.",
            nullable=True,
            max_length=8,
        ),
    },
    required=["project"],
    additional_properties=False,
).to_json_schema())
class ProjectUpdateTool(_ProjectTool):
    """Rename a project or change its description."""

    @property
    def name(self) -> str:
        return "project_update"

    @property
    def description(self) -> str:
        return (
            "Rename a project or update its description. The project ID, its directory "
            "and its history stay unchanged, so links and files keep working."
        )

    async def execute(
        self,
        project: str,
        name: str | None = None,
        description: str | None = None,
        emoji: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(project)
            updated = self._projects.update(
                target.id, name=name, description=description, emoji=emoji,
            )
        except ProjectError as exc:
            return ToolResult.error(f"Error: {exc}")
        return self._json({"project": updated.as_dict(), "status": "updated"})


@tool_parameters(tool_parameters_schema(
    project=StringSchema("Project name or ID."),
    confirm=BooleanSchema(
        description=(
            "Set to true only after the user has explicitly agreed to delete this "
            "project and everything in it."
        ),
    ),
    required=["project", "confirm"],
))
class ProjectDeleteTool(_ProjectTool):
    """Delete a project after explicit confirmation."""

    @property
    def name(self) -> str:
        return "project_delete"

    @property
    def description(self) -> str:
        return (
            "Delete a project and everything in it: its conversation, its files and its "
            "own long-term knowledge. Global memory about the user is kept. Always ask "
            "the user to confirm first; call again with confirm=true only after they "
            "agree."
        )

    async def execute(self, project: str, confirm: bool = False, **kwargs: Any) -> str:
        try:
            target = self._resolve(project)
        except ProjectError as exc:
            return ToolResult.error(f"Error: {exc}")
        if not confirm:
            return self._json({
                "status": "confirmation_required",
                "project": target.as_dict(),
                "message": (
                    "Deleting this project removes its conversation, files and project "
                    "memory. Ask the user to confirm before calling again with "
                    "confirm=true."
                ),
            })
        try:
            result = await self._projects.delete(target.id)
        except (ProjectError, KeyError) as exc:
            return ToolResult.error(f"Error: {exc}")
        return self._json({"status": "deleted", **result})


@tool_parameters(tool_parameters_schema(
    project=StringSchema("Project name or ID that should continue the work."),
    brief=StringSchema(
        "The task to continue: the goal, the requirements already agreed, and any "
        "constraints. Summarise; do not paste the conversation.",
        max_length=4_000,
    ),
    sources=ArraySchema(
        StringSchema("Message ID from the current conversation."),
        description=(
            "IDs of the messages this handoff came from, so the project can trace its "
            "origin. Use message IDs shown in the conversation context."
        ),
        max_items=20,
        nullable=True,
    ),
    required=["project", "brief"],
))
class ProjectHandoffTool(_ProjectTool):
    """Hand work from one conversation to a project."""

    @property
    def name(self) -> str:
        return "project_handoff"

    @property
    def description(self) -> str:
        return (
            "Hand a task from the current conversation to a project, recording the "
            "brief and the messages it came from. The project's next turn sees this "
            "handoff; the original conversation is not copied or deleted."
        )

    async def execute(
        self,
        project: str,
        brief: str,
        sources: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(project)
        except ProjectError as exc:
            return ToolResult.error(f"Error: {exc}")
        scope = self._scope()
        refs: list[str] = []
        for message_id in sources or []:
            if message_id.strip():
                refs.append(message_id.strip())
        try:
            result = self._projects.handoff(
                target.id, brief=brief, source_refs=refs,
            )
        except ProjectError as exc:
            return ToolResult.error(f"Error: {exc}")
        result["from_scope"] = scope
        return self._json(result)
