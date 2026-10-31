"""Agent-facing project tools, exercised through the real project service."""

from __future__ import annotations

import json

import pytest

from yoreni.agent.tools.context import RequestContext
from yoreni.agent.tools.projects import (
    ProjectCreateTool,
    ProjectDeleteTool,
    ProjectHandoffTool,
    ProjectListTool,
    ProjectUpdateTool,
)
from yoreni.projects.scope import GLOBAL_SCOPE


def tool(cls, env, *, scope: str = GLOBAL_SCOPE):
    instance = cls(env.projects)
    instance.set_context(RequestContext(
        channel="websocket", chat_id="yoreni", attributes={"memory_scope": scope},
    ))
    return instance


@pytest.mark.asyncio
async def test_create_tool_creates_the_project_and_its_directory(env) -> None:
    result = json.loads(await tool(ProjectCreateTool, env).execute(name="Thesis"))

    project_id = result["project"]["id"]
    assert result["status"] == "created"
    assert env.projects.directory(project_id).is_dir()
    assert result["project"]["link"] == f"/#/projects/{project_id}"


@pytest.mark.asyncio
async def test_delete_tool_requires_confirmation_before_removing_anything(env) -> None:
    project = env.create("Thesis")
    instance = tool(ProjectDeleteTool, env)

    refused = json.loads(await instance.execute(project=project.name, confirm=False))
    assert refused["status"] == "confirmation_required"
    assert env.projects.get(project.id) is not None
    assert env.projects.directory(project.id).is_dir()

    confirmed = json.loads(await instance.execute(project="Thesis", confirm=True))
    assert confirmed["status"] == "deleted"
    assert env.projects.get(project.id) is None


@pytest.mark.asyncio
async def test_handoff_records_the_brief_and_its_sources_in_the_project(env) -> None:
    project = env.create("Yororen")
    instance = tool(ProjectHandoffTool, env, scope=project.scope)

    result = json.loads(await instance.execute(
        project="Yororen", brief="Continue the homepage redesign", sources=["msg-1", "msg-2"],
    ))

    assert result["handoff"]["source_refs"] == ["msg-1", "msg-2"]
    assert result["from_scope"] == project.scope
    rendered = "\n".join(env.projects.context_lines(project))
    assert "Continue the homepage redesign" in rendered
    assert "msg-1" in rendered


@pytest.mark.asyncio
async def test_rename_keeps_the_link_and_directory(env) -> None:
    project = env.create("Thesis")

    result = json.loads(await tool(ProjectUpdateTool, env).execute(
        project=project.name, name="Undergraduate thesis",
    ))

    assert result["project"]["id"] == project.id
    assert result["project"]["name"] == "Undergraduate thesis"
    assert env.projects.directory(project.id).is_dir()


@pytest.mark.asyncio
async def test_ambiguous_project_names_ask_instead_of_guessing(env) -> None:
    env.create("Thesis")
    env.create("Thesis draft")

    result = await tool(ProjectUpdateTool, env).execute(project="Thes", name="Renamed")

    assert result.startswith("Error:")
    assert "several projects" in result


@pytest.mark.asyncio
async def test_list_tool_reports_the_current_scope(env) -> None:
    project = env.create("Thesis")

    result = json.loads(await tool(ProjectListTool, env, scope=project.scope).execute())

    assert [item["id"] for item in result["projects"]] == [project.id]
    assert result["current_scope"] == project.scope
