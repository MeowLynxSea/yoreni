"""Small convenience clients exposed by the high-level Python SDK."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yoreni.memory.core import MEMORY_PATH
from yoreni.runtime_context import RuntimeContextProvider

if TYPE_CHECKING:
    from yoreni.agent.loop import AgentLoop


class MemoryClient:
    """Long-term memory helpers exposed through ``bot.memory``."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    def read(self) -> str:
        """Read the always-visible ``memory/MEMORY.md`` summary."""
        return self._loop.memory.core.read_memory()

    def write(self, text: str) -> None:
        """Replace ``memory/MEMORY.md`` through the maintained core path."""
        self._loop.memory.core.write(MEMORY_PATH, text)

    async def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Search durable facts and return their provenance."""
        hits = await self._loop.memory.search(query, limit=limit)
        return [hit.to_dict() for hit in hits]

    async def remember(
        self,
        *,
        run_id: str,
        subject: str,
        predicate: str,
        text: str,
        kind: str = "user_fact",
        scope: str = "global",
        evidence_ids: Sequence[str] | None = None,
    ) -> list[str]:
        """Store one fact with evidence from the given Run.

        Pass ``evidence_ids`` for tool results that established the fact;
        without it the Run's user message is cited.
        """
        from yoreni.memory.models import FactKind

        return await self._loop.memory.remember(
            run_id=run_id,
            subject=subject,
            predicate=predicate,
            text=text,
            kind=FactKind(kind),
            scope=scope,
            evidence_ids=None if evidence_ids is None else list(evidence_ids),
        )

    def stats(self) -> dict[str, Any]:
        """Report backlog and consolidation state."""
        return deepcopy(self._loop.memory.stats())


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

