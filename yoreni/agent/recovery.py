"""Portable Run checkpoint recovery without repeating uncertain effects."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast


def checkpoint_history(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project a run checkpoint without repeating uncertain tool side effects."""
    assistant = cast(object, data.get("assistant_message"))
    completed_value = cast(object, data.get("completed_tool_results"))
    pending_value = cast(object, data.get("pending_tool_calls"))
    completed = cast(list[object], completed_value) if isinstance(completed_value, list) else []
    pending = cast(list[object], pending_value) if isinstance(pending_value, list) else []

    restored: list[dict[str, Any]] = []
    if isinstance(assistant, dict):
        assistant_row = cast(dict[str, Any], assistant)
    else:
        assistant_row = {}
    if assistant_row.get("role") == "assistant":
        row = dict(assistant_row)
        row.setdefault("timestamp", datetime.now().isoformat())
        restored.append(row)
    for value in completed:
        if not isinstance(value, dict):
            continue
        tool_result = cast(dict[str, Any], value)
        if tool_result.get("role") != "tool":
            continue
        row = dict(tool_result)
        row.setdefault("timestamp", datetime.now().isoformat())
        restored.append(row)
    for value in pending:
        if not isinstance(value, dict):
            continue
        tool_call = cast(dict[str, Any], value)
        tool_call_id = tool_call.get("id")
        function_value = cast(object, tool_call.get("function"))
        if not isinstance(tool_call_id, str) or not tool_call_id:
            continue
        function = (
            cast(dict[str, Any], function_value)
            if isinstance(function_value, dict)
            else {}
        )
        name = function.get("name")
        restored.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name if isinstance(name, str) and name else "tool",
                "content": "Error: Task interrupted before this tool finished.",
                "timestamp": datetime.now().isoformat(),
                "_recovery_interrupted": True,
            }
        )

    return restored

