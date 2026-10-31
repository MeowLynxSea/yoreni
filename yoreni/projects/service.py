"""Project lifecycle: durable metadata, an isolated directory, scoped memory.

The personal agent stays a single continuous identity. A project only files one
line of work under its own scope, gives that work a directory, and lets its
knowledge accumulate without leaking into another project's context.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from yoreni.agent.state import AgentStore
from yoreni.projects.models import Project
from yoreni.projects.scope import ProjectScopeError, normalize_chat_scope, project_scope
from yoreni.utils.helpers import safe_filename

if TYPE_CHECKING:
    from yoreni.memory.service import MemoryService

PROJECTS_DIRNAME = "projects"
UPLOADS_DIRNAME = "uploads"
MAX_NAME_CHARS = 80
MAX_DESCRIPTION_CHARS = 2_000
MAX_EMOJI_CHARS = 8
MAX_BRIEF_CHARS = 4_000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ProjectError(ValueError):
    """Raised when a project request cannot be honoured."""


def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        raise ProjectError("project name must be a string")
    name = " ".join(value.split())
    if not name:
        raise ProjectError("project name must not be empty")
    if len(name) > MAX_NAME_CHARS:
        raise ProjectError(f"project name must be at most {MAX_NAME_CHARS} characters")
    if _CONTROL_CHARS.search(name):
        raise ProjectError("project name contains control characters")
    return name


def _clean_description(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProjectError("project description must be a string")
    description = value.strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ProjectError(
            f"project description must be at most {MAX_DESCRIPTION_CHARS} characters"
        )
    if _CONTROL_CHARS.search(description):
        raise ProjectError("project description contains control characters")
    return description


def _clean_emoji(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProjectError("project emoji must be a string")
    emoji = value.strip()
    if not emoji:
        return ""
    if len(emoji) > MAX_EMOJI_CHARS:
        raise ProjectError(f"project emoji must be at most {MAX_EMOJI_CHARS} characters")
    if _CONTROL_CHARS.search(emoji):
        raise ProjectError("project emoji contains control characters")
    return emoji


class ProjectService:
    """Own the project collection, its directories and its memory scope."""

    def __init__(
        self,
        workspace: Path,
        store: AgentStore,
        *,
        memory: "MemoryService | None" = None,
    ) -> None:
        self.workspace = workspace
        self.store = store
        self.memory = memory
        self._cancel_run: Callable[[str], Awaitable[int]] | None = None

    def bind_cancel_run(self, cancel: Callable[[str], Awaitable[int]]) -> None:
        """Let deletion stop Runs this gateway is executing right now."""
        self._cancel_run = cancel

    # -- directories ---------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.workspace / PROJECTS_DIRNAME

    def directory(self, project_id: str) -> Path:
        """Return one project's working directory, refusing anything outside it.

        The ID is validated before use, so the path cannot climb out of the
        projects root; the containment check additionally rejects a directory
        that a symlink redirected somewhere else.
        """
        project_scope(project_id)  # validates the ID shape
        candidate = (self.root / project_id).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if candidate.parent != root:
            raise ProjectError("project directory escaped the projects root")
        return candidate

    def ensure_directory(self, project_id: str) -> Path:
        directory = self.directory(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        uploads = directory / UPLOADS_DIRNAME
        uploads.mkdir(exist_ok=True)
        # A symlinked uploads directory would let an attachment write outside
        # the project, so the boundary is checked again after creation.
        if uploads.is_symlink() or uploads.resolve(strict=False).parent != directory:
            raise ProjectError("project uploads directory escaped the project root")
        return directory

    # -- reads ---------------------------------------------------------------

    def list(self) -> list[Project]:
        return self.store.projects()

    def get(self, project_id: str) -> Project | None:
        try:
            project_scope(project_id)
        except ProjectScopeError:
            return None
        return self.store.project(project_id)

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise ProjectError(f"unknown project {project_id!r}")
        return project

    def find(self, query: str) -> list[Project]:
        """Resolve a project by ID, exact name, or a unique partial name."""
        text = " ".join(query.split())
        if not text:
            return []
        candidate = self.get(text)
        if candidate is not None:
            return [candidate]
        folded = text.casefold()
        projects = self.store.projects()
        exact = [item for item in projects if item.name.casefold() == folded]
        if exact:
            return exact
        return [item for item in projects if folded in item.name.casefold()]

    def scope_of(self, project_id: str) -> str:
        return self.require(project_id).scope

    # -- writes --------------------------------------------------------------

    def create(
        self, *, name: object, description: object = "", emoji: object = "",
    ) -> Project:
        cleaned = _clean_name(name)
        project = self.store.create_project(
            name=cleaned, description=_clean_description(description),
            emoji=_clean_emoji(emoji),
        )
        try:
            self.ensure_directory(project.id)
        except (OSError, ProjectError):
            # A project without a directory is not a project; keep the list
            # and the filesystem in the same state.
            self.store.delete_project(project.id)
            raise
        return project

    def update(
        self, project_id: str, *, name: object = None,
        description: object = None, emoji: object = None,
    ) -> Project:
        project = self.require(project_id)
        updated_name = None if name is None else _clean_name(name)
        updated_description = None if description is None else _clean_description(description)
        updated_emoji = None if emoji is None else _clean_emoji(emoji)
        if (
            updated_name == project.name
            and (updated_description is None or updated_description == project.description)
            and (updated_emoji is None or updated_emoji == project.emoji)
        ):
            return project
        return self.store.update_project(
            project_id, name=updated_name, description=updated_description,
            emoji=updated_emoji,
        )

    def handoff(
        self, project_id: str, *, brief: object, source_refs: Iterable[object] = (),
    ) -> dict[str, Any]:
        """Attach a task brief, and the messages it came from, to a project."""
        project = self.require(project_id)
        if not isinstance(brief, str) or not brief.strip():
            raise ProjectError("handoff brief must not be empty")
        text = brief.strip()
        if len(text) > MAX_BRIEF_CHARS:
            raise ProjectError(f"handoff brief must be at most {MAX_BRIEF_CHARS} characters")
        refs = [
            str(item) for item in source_refs
            if isinstance(item, str) and item.strip()
        ][:20]
        record = self.store.add_handoff(project.id, brief=text, source_refs=refs)
        return {"project": project.as_dict(), "handoff": record.as_dict()}

    async def delete(self, project_id: str) -> dict[str, Any]:
        """Delete a project, its conversations, its knowledge and its files."""
        project = self.require(project_id)
        scope = project.scope
        # Resolve the directory before removing anything: a redirected path
        # must stop the operation while the data is still intact.
        directory = self.directory(project.id)
        active = self.store.active_runs(scope)
        for run_id in active:
            if self._cancel_run is not None:
                try:
                    await self._cancel_run(run_id)
                except Exception:  # noqa: BLE001 - deletion must still finish
                    logger.exception("failed to cancel run {} for deleted project", run_id)
            self.store.interrupt(run_id, "Project deleted", cancelled=True)
        run_ids = await asyncio.to_thread(self.store.scoped_run_ids, scope)
        memory_result = (
            await asyncio.to_thread(
                self.memory.delete_project_scope, scope, run_ids=run_ids,
            )
            if self.memory is not None else {}
        )
        removed_runs = await asyncio.to_thread(self.store.delete_project, project.id)
        removed_directory = False
        if directory.is_dir():
            try:
                await asyncio.to_thread(shutil.rmtree, directory)
                removed_directory = True
            except OSError:
                logger.exception("failed to remove project directory {}", directory)
        root = self.root
        try:
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            # Another project may have appeared between the check and the call.
            pass
        return {
            "project": project.as_dict(),
            "runs": removed_runs,
            "cancelled_runs": active,
            "directory_removed": removed_directory,
            "memory": memory_result,
        }

    # -- project files -------------------------------------------------------

    def ingest_uploads(self, project_id: str, paths: Sequence[str]) -> list[str]:
        """Copy committed chat attachments into the project's own directory."""
        if not paths:
            return []
        uploads = self.ensure_directory(project_id) / UPLOADS_DIRNAME
        result: list[str] = []
        for raw in paths:
            source = Path(raw)
            if not source.is_file():
                result.append(raw)
                continue
            name = safe_filename(source.name).lstrip(".").strip() or "attachment"
            target = uploads / name
            counter = 1
            while target.exists():
                target = uploads / f"{Path(name).stem}-{counter}{Path(name).suffix}"
                counter += 1
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                raise ProjectError(f"could not store the attachment in the project: {exc}") from exc
            result.append(str(target))
        return result

    # -- context -------------------------------------------------------------

    def context_lines(self, project: Project, *, handoff_limit: int = 4) -> list[str]:
        """Render the project as stored data for the current turn."""
        lines = [
            f"Project: {project.name}",
            f"Project ID: {project.id}",
            "Working directory: " + str(self.directory(project.id)),
            "Project memory scope: " + project.scope,
        ]
        if project.description:
            lines.append(f"Description: {project.description}")
        handoffs = self.store.handoffs(project.id, limit=handoff_limit)
        if handoffs:
            payload = [
                {"brief": item.brief[:600], "sources": list(item.source_refs)[:8]}
                for item in handoffs
            ]
            lines.append(
                "Handed-over work (JSON, newest first): "
                + json.dumps(payload, ensure_ascii=False)
            )
        return lines

    @staticmethod
    def normalize_scope(value: object) -> str:
        return normalize_chat_scope(value)
