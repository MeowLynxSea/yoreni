"""Tools for the framework-owned Dream memory consolidation."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from yoreni.agent.memory import MemoryStore
from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from yoreni.cron.service import CronService
from yoreni.utils.gitstore import GitStoreError

_DREAM_JOB_ID = "dream"
_COMMIT_PREFIX = "dream:"
_DEFAULT_VERSIONS = 10
_MAX_VERSIONS = 20

_DREAM_PARAMETERS = tool_parameters_schema(
    action=StringSchema(
        "Action to perform", enum=["run", "versions", "restore"]
    ),
    commit=StringSchema(
        "REQUIRED when action='restore': commit id from action='versions'."
    ),
    limit=IntegerSchema(
        description="Maximum consolidations for action='versions'; defaults to 10.",
        minimum=1,
        maximum=_MAX_VERSIONS,
    ),
    required=["action"],
)

_background_runs: set[asyncio.Task[bool]] = set()


def _log_background_failure(task: asyncio.Task[bool]) -> None:
    _background_runs.discard(task)
    if task.cancelled():
        return
    if error := task.exception():
        logger.opt(exception=error).error("Dream run failed")


@tool_parameters(_DREAM_PARAMETERS)
class DreamTool(Tool):
    """Run memory consolidation and inspect or undo its results."""

    def __init__(self, memory: MemoryStore, cron_service: CronService | None) -> None:
        self._memory = memory
        self._cron_service = cron_service

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.memory is None:
            raise RuntimeError("DreamTool requires a MemoryStore")
        return cls(ctx.memory, ctx.cron_service)

    @property
    def name(self) -> str:
        return "dream"

    @property
    def description(self) -> str:
        return (
            "Memory consolidation. action='run' organizes recent conversation into durable "
            "memory in the background; use it only when the user asks to tidy or update "
            "memory. action='versions' lists recent consolidations. action='restore' undoes "
            "one consolidation; use it only when the user asks to undo it or it clearly "
            "damaged memory."
        )

    async def execute(
        self,
        action: str,
        commit: str | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> str:
        normalized = (action or "").strip().lower()
        if normalized == "run":
            return self._run()
        if normalized == "versions":
            return self._versions(limit)
        if normalized == "restore":
            return self._restore(commit)
        return ToolResult.error(
            "Error: action must be one of run, versions, or restore."
        )

    def _run(self) -> str:
        cron = self._cron_service
        if cron is None or cron.get_job(_DREAM_JOB_ID) is None:
            return ToolResult.error(
                "Error: memory consolidation is turned off in settings."
            )
        task = asyncio.create_task(cron.run_job(_DREAM_JOB_ID, force=True))
        _background_runs.add(task)
        task.add_done_callback(_log_background_failure)
        return "Memory consolidation started in the background."

    def _versions(self, limit: int | None) -> str:
        count = min(max(limit or _DEFAULT_VERSIONS, 1), _MAX_VERSIONS)
        try:
            commits = list(self._memory.git.log(
                max_entries=count, message_prefix=_COMMIT_PREFIX,
            ))
        except GitStoreError as exc:
            return ToolResult.error(f"Error: {exc}")
        if not commits:
            return "No memory consolidations yet."
        lines = [
            f"{item.sha}  {item.timestamp}  {item.message.splitlines()[0]}"
            for item in commits
        ]
        return "Recent memory consolidations:\n" + "\n".join(lines)

    def _restore(self, commit: str | None) -> str:
        sha = (commit or "").strip()
        if not sha:
            return ToolResult.error(
                "Error: action='restore' requires a commit from action='versions'."
            )
        try:
            created = self._memory.git.revert(
                sha, message_prefix=_COMMIT_PREFIX, message=f"dream: undo {sha}",
            )
        except GitStoreError as exc:
            return ToolResult.error(f"Error: {exc}")
        if created is None:
            return ToolResult.error(
                "Error: that commit is not a memory consolidation, or it cannot be undone."
            )
        return f"Memory restored. Recorded as {created}."
