"""Tools for framework-owned memory consolidation."""

# pyright: reportIncompatibleMethodOverride=false

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from yoreni.cron.service import CronService
from yoreni.memory.models import MemoryError
from yoreni.memory.service import MemoryService
from yoreni.utils.gitstore import GitStoreError

_DREAM_JOB_ID = "dream"
_DEFAULT_VERSIONS = 10
_MAX_VERSIONS = 20

_DREAM_PARAMETERS = tool_parameters_schema(
    action=StringSchema(
        "Action to perform", enum=["run", "versions", "restore", "proposals"]
    ),
    run_id=StringSchema(
        "REQUIRED when action='restore': consolidation id from action='versions'."
    ),
    limit=IntegerSchema(
        description="Maximum consolidations for action='versions'; defaults to 10.",
        minimum=1,
        maximum=_MAX_VERSIONS,
    ),
    required=["action"],
)

_background_runs: set[asyncio.Task[bool | None]] = set()


def _log_background_failure(task: asyncio.Task[bool | None]) -> None:
    _background_runs.discard(task)
    if task.cancelled():
        return
    if error := task.exception():
        logger.opt(exception=error).error("Dream run failed")


@tool_parameters(_DREAM_PARAMETERS)
class DreamTool(Tool):
    """Run consolidation and inspect or undo its recorded changes."""

    def __init__(self, memory: MemoryService, cron_service: CronService | None) -> None:
        self._memory = memory
        self._cron_service = cron_service

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory is not None and ctx.memory.config.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.memory is None:
            raise RuntimeError("DreamTool requires a MemoryService")
        return cls(ctx.memory, ctx.cron_service)

    @property
    def name(self) -> str:
        return "dream"

    @property
    def description(self) -> str:
        return (
            "Memory consolidation. action='run' consolidates pending facts and refreshes "
            "core memory in the background; use it only when the user asks to tidy or "
            "update memory. action='versions' lists recorded consolidations. "
            "action='restore' undoes one consolidation's facts and core-file changes. "
            "action='proposals' lists skill proposals waiting for human review; they are "
            "never installed automatically."
        )

    async def execute(
        self,
        action: str,
        run_id: str | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> str:
        normalized = (action or "").strip().lower()
        if normalized == "run":
            return self._run()
        if normalized == "versions":
            return self._versions(limit)
        if normalized == "restore":
            return self._restore(run_id)
        if normalized == "proposals":
            return self._proposals(limit)
        return ToolResult.error(
            "Error: action must be one of run, versions, restore, or proposals."
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
        versions = self._memory.store.dream_versions(count)
        if not versions:
            return "No memory consolidations yet."
        rows = []
        for version in versions:
            applied = version.get("applied") or {}
            rows.append({
                "run_id": version["id"],
                "completed_at": version["updated_at"],
                "changes": applied.get("changes", 0) if isinstance(applied, dict) else 0,
                "files": sorted((applied.get("files") or {}).keys())
                if isinstance(applied, dict) else [],
                "git_commit": version.get("git_commit"),
            })
        return json.dumps({"consolidations": rows}, ensure_ascii=False)

    def _proposals(self, limit: int | None) -> str:
        count = min(max(limit or _DEFAULT_VERSIONS, 1), _MAX_VERSIONS)
        proposals = self._memory.store.skill_proposals()[:count]
        if not proposals:
            return "No pending skill proposals."
        return json.dumps({"proposals": proposals}, ensure_ascii=False)

    def _restore(self, run_id: str | None) -> str:
        identifier = (run_id or "").strip()
        if not identifier:
            return ToolResult.error(
                "Error: action='restore' requires a consolidation id from action='versions'."
            )
        try:
            result = self._memory.restore_dream(identifier)
        except (MemoryError, GitStoreError) as exc:
            return ToolResult.error(f"Error: {exc}")
        return json.dumps(result, ensure_ascii=False)


__all__ = ["DreamTool"]
