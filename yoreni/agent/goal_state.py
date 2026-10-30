"""Agent state helpers for explicit sustained goals."""

from __future__ import annotations

import json
from typing import Any, Mapping, cast

GOAL_STATE_KEY = "goal_state"
MAX_GOAL_OBJECTIVE_CHARS = 4000
_MAX_OBJECTIVE_WS = 600


def goal_state_raw(state: Mapping[str, Any] | None) -> Any:
    """Read the Agent-owned sustained goal."""
    return state.get(GOAL_STATE_KEY) if state else None


def sustained_goal_active(metadata: Mapping[str, Any] | None) -> bool:
    """True when the Agent has an active sustained objective."""
    goal = parse_goal_state(goal_state_raw(metadata))
    return isinstance(goal, dict) and goal.get("status") == "active"


def parse_goal_state(blob: Any) -> dict[str, Any] | None:
    if blob is None:
        return None
    if isinstance(blob, dict):
        return cast(dict[str, Any], blob)
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
    return None


def goal_state_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """Lines appended inside the Runtime Context block for an open goal."""
    if not metadata:
        return []
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") not in {"active", "blocked"}:
        return []
    status = str(goal.get("status"))
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return [f"Goal ({status}): no objective text stored."]
    if len(objective) > MAX_GOAL_OBJECTIVE_CHARS:
        objective = objective[:MAX_GOAL_OBJECTIVE_CHARS].rstrip() + "\n… (truncated)"
    out = [f"Goal ({status}):", objective]
    hint = str(goal.get("ui_summary") or "").strip()
    if hint:
        out.append(f"Summary: {hint}")
    if status == "blocked":
        recap = str(goal.get("recap") or "").strip()
        if recap:
            out.append(f"Waiting on: {recap}")
    return out


def goal_state_ws_blob(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """JSON-safe snapshot for WebSocket ``goal_state`` events (one chat_id per frame)."""
    goal = parse_goal_state(goal_state_raw(metadata)) if metadata else None
    if isinstance(goal, dict) and goal.get("status") in {"active", "blocked"}:
        status = str(goal.get("status"))
        objective = str(goal.get("objective") or "").strip()
        if len(objective) > _MAX_OBJECTIVE_WS:
            objective = objective[:_MAX_OBJECTIVE_WS].rstrip() + "…"
        summary = str(goal.get("ui_summary") or "").strip()[:120]
        blob: dict[str, Any] = {"active": status == "active", "status": status}
        if summary:
            blob["ui_summary"] = summary
        if objective:
            blob["objective"] = objective
        recap = str(goal.get("recap") or "").strip()[:240]
        if recap:
            blob["recap"] = recap
        return blob
    return {"active": False}
