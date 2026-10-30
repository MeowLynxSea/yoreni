"""Agent-owned task state, independent of long-term memory.

Preferences, user facts and project knowledge live in the memory system. This
tool owns only the current task's working state and a small scratchpad, so the
same fact never has two storage locations.
"""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from yoreni.agent.state import AgentStore
from yoreni.agent.tools.base import Tool, ToolResult

_CONTROL_KEYS = ("goal_state", "thread_goal", "model_preset", "_sustained_goal_continuation_rounds")
_RUN_STATE_KEYS = ("task", "notes")
_MAX_NOTE_CHARS = 2_000
_MAX_TASK_CHARS = 4_000


class AgentStateTool(Tool):
    def __init__(self, store: AgentStore):
        self.store = store

    @property
    def name(self) -> str:
        return "agent_state"

    @property
    def description(self) -> str:
        return (
            "Read or update the current task's working state. "
            "Writable fields: 'task' (object describing progress) and 'notes' "
            "(list of short strings). Updates replace the state and require the "
            "revision returned by read. Durable facts and preferences belong to "
            "memory_remember, not here. Preserve goal and model control fields; "
            "use the goal tools and model settings to change them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "update"]},
                "revision": {"type": "integer", "minimum": 0},
                "state": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "object"},
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                },
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
            rejected = sorted(set(state) - set(_CONTROL_KEYS) - set(_RUN_STATE_KEYS))
            if rejected:
                return ToolResult.error(
                    "agent_state only stores current task progress. Move durable facts "
                    f"and preferences to memory_remember; unsupported keys: {', '.join(rejected)}"
                )
            problem = _validate_run_state(state)
            if problem:
                return ToolResult.error(problem)
            identity = await asyncio.to_thread(self.store.update, revision, state)
        else:
            identity = await asyncio.to_thread(self.store.identity)
        payload: dict[str, Any] = {
            "agent_id": identity.id,
            "revision": identity.revision,
            "state": identity.state,
        }
        legacy = sorted(set(identity.state) - set(_CONTROL_KEYS) - set(_RUN_STATE_KEYS))
        if legacy:
            payload["legacy_keys"] = legacy
            payload["legacy_note"] = (
                "These keys predate the memory split. Re-store them with memory_remember "
                "and omit them from the next update."
            )
        return json.dumps(payload, ensure_ascii=False)


def _validate_run_state(state: dict[str, Any]) -> str | None:
    task = state.get("task")
    if task is not None:
        if not isinstance(task, dict):
            return "task must be an object."
        if len(json.dumps(task, ensure_ascii=False)) > _MAX_TASK_CHARS:
            return f"task must stay under {_MAX_TASK_CHARS} characters."
    raw_notes = cast(object, state.get("notes"))
    if raw_notes is not None:
        if not isinstance(raw_notes, list):
            return "notes must be a list of strings."
        notes = list(cast(list[object], raw_notes))
        if not all(isinstance(note, str) for note in notes):
            return "notes must be a list of strings."
        if sum(len(note) for note in notes if isinstance(note, str)) > _MAX_NOTE_CHARS:
            return f"notes must stay under {_MAX_NOTE_CHARS} characters in total."
    return None
