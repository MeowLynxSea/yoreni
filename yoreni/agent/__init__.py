"""Agent core module."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yoreni.agent.context import ContextBuilder
    from yoreni.agent.hook import (
        AgentHook,
        AgentHookContext,
        AgentRunHookContext,
        AgentTurnHookContext,
        AgentTurnHookFactory,
        CompositeHook,
    )
    from yoreni.agent.loop import AgentLoop
    from yoreni.agent.skills import SkillsLoader


_EXPORTS = {
    "AgentHook": "yoreni.agent.hook",
    "AgentHookContext": "yoreni.agent.hook",
    "AgentRunHookContext": "yoreni.agent.hook",
    "AgentTurnHookContext": "yoreni.agent.hook",
    "AgentTurnHookFactory": "yoreni.agent.hook",
    "AgentLoop": "yoreni.agent.loop",
    "CompositeHook": "yoreni.agent.hook",
    "ContextBuilder": "yoreni.agent.context",
    "SkillsLoader": "yoreni.agent.skills",
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
    "AgentHook",
    "AgentHookContext",
    "AgentRunHookContext",
    "AgentTurnHookContext",
    "AgentTurnHookFactory",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "SkillsLoader",
]
