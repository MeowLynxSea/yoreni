"""Public tool contracts and discovery interfaces."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yoreni.agent.tools.base import Schema, Tool, ToolResult, tool_parameters
    from yoreni.agent.tools.context import ToolContext
    from yoreni.agent.tools.loader import ToolLoader
    from yoreni.agent.tools.registry import ToolRegistry
    from yoreni.agent.tools.schema import (
        ArraySchema,
        BooleanSchema,
        IntegerSchema,
        NumberSchema,
        ObjectSchema,
        StringSchema,
        tool_parameters_schema,
    )


_EXPORTS = {
    "Schema": "yoreni.agent.tools.base",
    "ArraySchema": "yoreni.agent.tools.schema",
    "BooleanSchema": "yoreni.agent.tools.schema",
    "IntegerSchema": "yoreni.agent.tools.schema",
    "NumberSchema": "yoreni.agent.tools.schema",
    "ObjectSchema": "yoreni.agent.tools.schema",
    "StringSchema": "yoreni.agent.tools.schema",
    "Tool": "yoreni.agent.tools.base",
    "ToolContext": "yoreni.agent.tools.context",
    "ToolLoader": "yoreni.agent.tools.loader",
    "ToolResult": "yoreni.agent.tools.base",
    "ToolRegistry": "yoreni.agent.tools.registry",
    "tool_parameters": "yoreni.agent.tools.base",
    "tool_parameters_schema": "yoreni.agent.tools.schema",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    "Schema",
    "ArraySchema",
    "BooleanSchema",
    "IntegerSchema",
    "NumberSchema",
    "ObjectSchema",
    "StringSchema",
    "Tool",
    "ToolContext",
    "ToolLoader",
    "ToolResult",
    "ToolRegistry",
    "tool_parameters",
    "tool_parameters_schema",
]
