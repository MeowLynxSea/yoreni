"""Sustained-goal tools with explicit user opt-in at the execution boundary."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from yoreni.agent.goal_permission import (
    goal_mutation_allowed,
    revoke_goal_mutation_permission,
)
from yoreni.agent.goal_state import (
    GOAL_STATE_KEY,
    MAX_GOAL_OBJECTIVE_CHARS,
    explicit_goal_requested,
    goal_state_raw,
    goal_state_runtime_lines,
    parse_goal_state,
    sustained_goal_active,
)
from yoreni.agent.state import AgentStore
from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import RequestContext, ToolContext, current_request_context
from yoreni.agent.tools.schema import StringSchema, tool_parameters_schema
from yoreni.bus.queue import MessageBus
from yoreni.bus.runtime_events import GoalStateChanged
from yoreni.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from yoreni.utils.cancellation import shield_and_drain
from yoreni.utils.prompt_templates import render_template

_GOAL_ACTIONS = ("complete", "cancel", "block", "replace")
_CREATE_UNAVAILABLE_ERROR = (
    "Error: create_goal is unavailable for this turn. Ask the user to start a goal."
)
_REPLACE_UNAVAILABLE_ERROR = (
    "Error: replacing the goal is unavailable for this turn. Ask the user to start "
    "a replacement goal."
)


@dataclass
class _GoalTarget:
    metadata: dict[str, Any]
    revision: int


def _iso_now() -> str:
    return datetime.now().isoformat()


class _GoalToolsMixin:
    """Persist goals on the Agent with revision-checked writes."""

    def __init__(self, agent_store: AgentStore, bus: MessageBus | None = None) -> None:
        self._bus = bus
        self._agent_store = agent_store

    async def _target(self) -> _GoalTarget:
        identity = await asyncio.to_thread(self._agent_store.identity)
        return _GoalTarget(identity.state, identity.revision)

    def _goal_mutation_allowed(self) -> bool:
        return current_request_context() is not None and goal_mutation_allowed()

    async def _save_goal_state(
        self,
        target: _GoalTarget,
        blob: dict[str, Any],
        *,
        revoke_permission: bool = False,
    ) -> None:
        saved = False

        async def save_and_publish() -> None:
            nonlocal saved
            state = {**target.metadata, GOAL_STATE_KEY: deepcopy(blob)}
            identity = await asyncio.to_thread(
                self._agent_store.update, target.revision, state,
            )
            target.metadata = identity.state
            target.revision = identity.revision
            saved = True
            await self._publish_goal_state_changed(target.metadata)

        try:
            await shield_and_drain(save_and_publish())
        finally:
            # This ContextVar belongs to the caller task, not the settlement task.
            if revoke_permission and saved:
                revoke_goal_mutation_permission()

    async def _publish_goal_state_changed(self, metadata: dict[str, Any]) -> None:
        bus = self._bus
        rc = current_request_context()
        if bus is None or rc is None:
            return
        await bus.publish(GoalStateChanged(
            agent_id=rc.agent_id or self._agent_store.identity().id,
            run_id=rc.turn_id,
            state=deepcopy(metadata),
        ))


@tool_parameters(
    tool_parameters_schema(
        objective=StringSchema(
            "Self-contained goal with explicit completion criteria, bounded and safe to repeat. "
            "May incorporate prior discussion.",
            min_length=1,
            max_length=MAX_GOAL_OBJECTIVE_CHARS,
        ),
        ui_summary=StringSchema(
            "One-line display label for the goal and logs; objective defines the goal.",
            max_length=120,
            nullable=True,
        ),
        required=["objective"],
    )
)
class CreateGoalTool(Tool, _GoalToolsMixin):
    """Create one explicit sustained objective for the Agent."""

    def __init__(
        self,
        agent_store: AgentStore,
        bus: MessageBus | None = None,
    ) -> None:
        _GoalToolsMixin.__init__(self, agent_store, bus)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.agent_store is None:
            raise RuntimeError("CreateGoalTool requires an AgentStore")
        return cls(ctx.agent_store, ctx.bus)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.agent_store is not None

    @property
    def name(self) -> str:
        return "create_goal"

    @property
    def description(self) -> str:
        return (
            "Create one sustained goal for the Agent only when Goal Runtime Guidance "
            "asks you to record it. Do not retry after successful creation."
        )

    def runtime_context_provider(self):
        return self._provide_runtime_context

    async def _provide_runtime_context(
        self,
        request: RequestContext,
    ) -> RuntimeContextBlock | None:
        target = await self._target()
        metadata = target.metadata
        goal_start_requested = explicit_goal_requested(request.metadata)
        goal_active = sustained_goal_active(metadata)
        if not goal_start_requested and not goal_active:
            return None

        guidance = render_template(
            "agent/goal_runtime.md",
            strip=True,
            goal_start_requested=goal_start_requested,
            goal_active=goal_active,
        )
        state = wrap_runtime_context_lines(goal_state_runtime_lines(metadata))
        content = "\n\n".join(part for part in (guidance, state) if part)
        return RuntimeContextBlock(source="goal", content=content)

    async def execute(
        self,
        objective: str,
        ui_summary: str | None = None,
        **kwargs: Any,
    ) -> str:
        sess = await self._target()
        if not self._goal_mutation_allowed():
            return ToolResult.error(_CREATE_UNAVAILABLE_ERROR)
        prior = parse_goal_state(goal_state_raw(sess.metadata))
        if isinstance(prior, dict) and prior.get("status") == "active":
            return ToolResult.error(
                "Error: a sustained goal is already active. Use update_goal with "
                "action='replace' only if the user explicitly changes the objective."
            )

        objective_text = objective.strip()
        if not objective_text:
            return ToolResult.error("Error: objective must not be empty.")
        if len(objective_text) > MAX_GOAL_OBJECTIVE_CHARS:
            return ToolResult.error(
                f"Error: objective must not exceed {MAX_GOAL_OBJECTIVE_CHARS} characters."
            )
        summary = (ui_summary or "").strip()[:120]
        blob = {
            "status": "active",
            "objective": objective_text,
            "ui_summary": summary,
            "started_at": _iso_now(),
        }
        await self._save_goal_state(sess, blob)
        extra = f"\nSummary line: {summary}" if summary else ""
        return (
            "Goal recorded. Keep working toward the objective using ordinary tools. "
            "When fully done and verified, call update_goal with action='complete'."
            f"{extra}"
        )


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "How to update the active goal.",
            enum=_GOAL_ACTIONS,
        ),
        recap=StringSchema(
            "Brief honest recap for the user. Required in practice for complete, cancel, and block.",
            max_length=8000,
            nullable=True,
        ),
        objective=StringSchema(
            "Replacement objective. Required only when action is 'replace'; make it durable, "
            "self-contained, bounded, and explicit about done-ness.",
            max_length=MAX_GOAL_OBJECTIVE_CHARS,
            nullable=True,
        ),
        ui_summary=StringSchema(
            "Optional one-line display label for a replacement goal.",
            max_length=120,
            nullable=True,
        ),
        required=["action"],
    )
)
class UpdateGoalTool(Tool, _GoalToolsMixin):
    """Complete, cancel, block, or replace the active sustained goal."""

    def __init__(
        self,
        agent_store: AgentStore,
        bus: MessageBus | None = None,
    ) -> None:
        _GoalToolsMixin.__init__(self, agent_store, bus)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.agent_store is None:
            raise RuntimeError("UpdateGoalTool requires an AgentStore")
        return cls(ctx.agent_store, ctx.bus)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.agent_store is not None

    @property
    def name(self) -> str:
        return "update_goal"

    @property
    def description(self) -> str:
        return (
            "Update the active sustained goal. Use action='complete' only after the objective "
            "is actually achieved and verified. Use action='cancel' when the user cancels, "
            "action='block' when progress is genuinely blocked, and action='replace' only when "
            "the requested objective changes."
        )

    async def execute(
        self,
        action: str,
        recap: str | None = None,
        objective: str | None = None,
        ui_summary: str | None = None,
        **kwargs: Any,
    ) -> str:
        sess = await self._target()
        prior = parse_goal_state(goal_state_raw(sess.metadata))
        if not isinstance(prior, dict) or prior.get("status") != "active":
            return "No active goal to update."

        normalized = (action or "").strip().lower()
        if normalized not in _GOAL_ACTIONS:
            return ToolResult.error(
                "Error: action must be one of complete, cancel, block, or replace."
            )

        if normalized == "replace":
            if not self._goal_mutation_allowed():
                return ToolResult.error(_REPLACE_UNAVAILABLE_ERROR)
            objective_text = (objective or "").strip()
            if not objective_text:
                return ToolResult.error(
                    "Error: update_goal action='replace' requires a replacement objective."
                )
            if len(objective_text) > MAX_GOAL_OBJECTIVE_CHARS:
                return ToolResult.error(
                    f"Error: objective must not exceed {MAX_GOAL_OBJECTIVE_CHARS} characters."
                )
            summary = (ui_summary or "").strip()[:120]
            blob = {
                "status": "active",
                "objective": objective_text,
                "ui_summary": summary,
                "started_at": _iso_now(),
                "replaced_at": _iso_now(),
                "previous_objective": str(prior.get("objective") or ""),
                "recap": (recap or "").strip(),
            }
            await self._save_goal_state(sess, blob)
            extra = f"\nSummary line: {summary}" if summary else ""
            return "Goal replaced. Continue toward the new objective using ordinary tools." + extra

        ended = _iso_now()
        status = {
            "complete": "completed",
            "cancel": "cancelled",
            "block": "blocked",
        }[normalized]
        blob = {
            **prior,
            "status": status,
            "ended_at": ended,
            "recap": (recap or "").strip(),
        }
        if normalized == "complete":
            blob["completed_at"] = ended
        await self._save_goal_state(sess, blob, revoke_permission=True)

        tail = (recap or "").strip()
        label = {
            "complete": "complete",
            "cancel": "cancelled",
            "block": "blocked",
        }[normalized]
        if tail:
            return f"Goal marked {label} ({ended}). Recap:\n{tail}"
        return f"Goal marked {label} ({ended})."
