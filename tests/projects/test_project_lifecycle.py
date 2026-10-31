"""Project lifecycle: durable identity, own directory, deletable state."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from project_helpers import ProjectEnv

from yoreni.agent.state import AgentInput
from yoreni.projects.service import ProjectError


def test_create_and_rename_keep_the_id_and_directory(env: ProjectEnv):
    project = env.create("  Thesis   draft ", description="Chapter notes")

    assert project.name == "Thesis draft"
    assert project.description == "Chapter notes"
    directory = env.workspace / "projects" / project.id
    assert directory.is_dir()
    assert (directory / "uploads").is_dir()
    assert env.projects.directory(project.id) == directory.resolve()
    assert [item.id for item in env.projects.find("thesis")] == [project.id]

    renamed = env.projects.update(project.id, name="Dissertation")

    assert renamed.id == project.id
    assert env.projects.require(project.id).name == "Dissertation"
    assert env.projects.directory(project.id) == directory.resolve()


def test_emoji_is_optional_and_survives_rename(env: ProjectEnv):
    project = env.create("Thesis", emoji="📄")

    assert env.projects.require(project.id).emoji == "📄"
    assert env.projects.update(project.id, name="Dissertation").emoji == "📄"
    assert env.projects.update(project.id, emoji="").emoji == ""
    with pytest.raises(ProjectError):
        env.projects.update(project.id, emoji="too many characters")


def test_ingest_uploads_copies_attachments_into_the_project_directory(env: ProjectEnv):
    project = env.create("Research")
    inbox = env.workspace / "inbox"
    (inbox / "later").mkdir(parents=True)
    first = inbox / "diagram.png"
    first.write_bytes(b"first")
    second = inbox / "later" / "diagram.png"
    second.write_bytes(b"second")

    stored = env.projects.ingest_uploads(project.id, [str(first), str(second)])

    uploads = env.projects.directory(project.id) / "uploads"
    assert [Path(item).parent for item in stored] == [uploads, uploads]
    assert [Path(item).name for item in stored] == ["diagram.png", "diagram-1.png"]
    assert [Path(item).read_bytes() for item in stored] == [b"first", b"second"]
    assert first.exists() and second.exists()


async def test_delete_removes_metadata_runs_rows_and_directory(env: ProjectEnv):
    project = env.create("Archive")
    env.projects.handoff(project.id, brief="Write the introduction", source_refs=["msg-1"])
    env.turn("draft the abstract", scope=project.scope)

    result = await env.projects.delete(project.id)

    assert result["runs"] == 1
    assert result["directory_removed"] is True
    assert env.projects.get(project.id) is None
    assert env.projects.list() == []
    assert not (env.workspace / "projects" / project.id).exists()
    assert env.store.scoped_run_ids(project.scope) == []
    assert env.store.webui_history(scope=project.scope) == []
    assert env.store.conversation_rows(scope=project.scope) == []
    assert env.store.handoffs(project.id) == []
    with pytest.raises(ValueError, match="unknown project"):
        env.store.admit(AgentInput(
            source="webui", content="again", principal="owner", scope=project.scope,
        ))


async def test_delete_interrupts_active_runs_and_removes_their_rows(env: ProjectEnv):
    project = env.create("Busy")
    queued = env.store.admit(AgentInput(
        source="webui", content="queued work", principal="owner",
        event_id=uuid4().hex, scope=project.scope,
    ))
    running = env.store.admit(AgentInput(
        source="webui", content="running work", principal="owner",
        event_id=uuid4().hex, scope=project.scope,
    ))
    env.store.start(running, [{"role": "user", "content": "running work"}])
    cancelled: list[str] = []

    async def cancel(run_id: str) -> int:
        cancelled.append(run_id)
        return 1

    env.projects.bind_cancel_run(cancel)
    result = await env.projects.delete(project.id)

    assert cancelled == [queued, running]
    assert result["cancelled_runs"] == [queued, running]
    assert env.store.pending(scope=project.scope) == []
    assert env.store.scoped_run_ids(project.scope) == []
    with pytest.raises(KeyError):
        env.store.read_run(queued)


async def test_delete_finishes_even_when_a_run_cannot_be_cancelled(env: ProjectEnv):
    project = env.create("Busy")
    run_id = env.store.admit(AgentInput(
        source="webui", content="running work", principal="owner",
        event_id=uuid4().hex, scope=project.scope,
    ))
    env.store.start(run_id, [{"role": "user", "content": "running work"}])

    async def cancel(_run_id: str) -> int:
        raise RuntimeError("the runner is already gone")

    env.projects.bind_cancel_run(cancel)
    result = await env.projects.delete(project.id)

    assert result["cancelled_runs"] == [run_id]
    assert env.store.scoped_run_ids(project.scope) == []
    assert env.projects.get(project.id) is None
