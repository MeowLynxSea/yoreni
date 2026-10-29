"""Small convenience clients exposed by the high-level Python SDK."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yoreni.runtime_context import RuntimeContextProvider

if TYPE_CHECKING:
    from yoreni.agent.loop import AgentLoop


class MemoryClient:
    """Long-term memory helpers exposed through ``bot.memory``."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    def read(self) -> str:
        """Read ``memory/MEMORY.md``."""
        return self._loop.context.memory.read_memory()

    def write(self, text: str) -> None:
        """Overwrite ``memory/MEMORY.md``."""
        self._loop.context.memory.write_memory(text)

    def append_history(self, text: str) -> int:
        """Append one entry to ``memory/history.jsonl`` and return its cursor."""
        return self._loop.context.memory.append_history(text)

    def read_history(self) -> list[dict[str, Any]]:
        """Read the agent memory journal."""
        entries = self._loop.context.memory.read_unprocessed_history(since_cursor=0)
        return deepcopy(entries)


class RuntimeClient:
    """Runtime control helpers exposed through ``bot.runtime``."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    @property
    def model(self) -> str:
        """Current runtime model name."""
        return self._loop.model

    @property
    def workspace(self) -> Path:
        """Current runtime workspace."""
        return self._loop.workspace

    def add_context_provider(
        self,
        provider: RuntimeContextProvider,
    ) -> Callable[[], None]:
        """Register per-turn model context and return an unsubscribe callback."""
        return self._loop.register_runtime_context_provider(provider)

