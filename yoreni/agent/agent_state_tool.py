"""Agent-owned preferences and task state, independent of conversation metadata."""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import json
from typing import Any

from yoreni.agent.state import AgentStore
from yoreni.agent.tools.base import Tool, ToolResult

_CONTROL_KEYS = ("goal_state", "thread_goal", "model_preset", "_sustained_goal_continuation_rounds")


class AgentStateTool(Tool):
    def __init__(self, store: AgentStore):
        self.store = store

    @property
    def name(self) -> str:
        return "agent_state"

    @property
    def description(self) -> str:
        return (
            "Read or update your durable preferences and task state. "
            "Updates replace the state and require the revision returned by read. "
            "On conflict, read the latest state and merge your changes. "
            "Preserve goal and model control fields; use goal tools and the model settings to change them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "update"]},
                "revision": {"type": "integer", "minimum": 0},
                "state": {"type": "object"},
            },
            "required": ["action"],
        }

    async def execute(
        self, action: str, revision: int | None = None,
        state: dict[str, Any] | None = None, **kwargs: Any,
    ) -> str:
        if action == "update":
            if revision is None or state is None:
                return ToolResult.error("An update requires revision and state.")
            current = await asyncio.to_thread(self.store.identity)
            if any(state.get(key) != current.state.get(key) for key in _CONTROL_KEYS):
                return ToolResult.error(
                    "Preserve goal and model control fields. Use create_goal/update_goal "
                    "for sustained goals and the model settings for model selection."
                )
            identity = await asyncio.to_thread(self.store.update, revision, state)
        else:
            identity = await asyncio.to_thread(self.store.identity)
        return json.dumps({
            "agent_id": identity.id, "revision": identity.revision, "state": identity.state,
        }, ensure_ascii=False)
