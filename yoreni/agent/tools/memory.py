"""Memory tools: search, read, remember, correct and forget.

Every write still goes through the same extraction-grade validation as
automatic memory: the tool supplies a claim, the service attaches evidence from
the current Run and the store reconciles it with existing facts.
"""

# pyright: reportIncompatibleMethodOverride=false
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ContextAware, RequestContext, ToolContext
from yoreni.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.memory.models import (
    EvidenceKind,
    FactKind,
    FactStatus,
    MemoryError,
    fts_match_query,
)
from yoreni.memory.service import MemoryService

_KIND_VALUES = [kind.value for kind in FactKind]
_STATUS_VALUES = [status.value for status in FactStatus]


class _MemoryTool(Tool, ContextAware):
    """Shared plumbing: the service handle and the current Run id."""

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory
        self._context: RequestContext | None = None

    def set_context(self, ctx: RequestContext) -> None:
        self._context = ctx

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory is not None and ctx.memory.config.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.memory is None:
            raise RuntimeError(f"{cls.__name__} requires a MemoryService")
        return cls(ctx.memory)

    def _run_id(self) -> str:
        run_id = self._context.turn_id if self._context is not None else None
        if not run_id:
            raise MemoryError("memory writes require an active Run")
        return run_id

    def _scope(self) -> str | None:
        if self._context is None:
            return None
        return self._context.attributes.get("memory_scope") if isinstance(
            self._context.attributes.get("memory_scope"), str
        ) else None

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)


@tool_parameters(tool_parameters_schema(
    query=StringSchema("What to look up in long-term memory."),
    scope=StringSchema(
        "Optional scope filter, e.g. 'project:yoreni'. Global facts always match.",
    ),
    kind=StringSchema("Optional fact kind filter.", enum=_KIND_VALUES),
    status=StringSchema(
        "Which lifecycle states to search. 'current' returns active facts only; "
        "'history' includes dormant, superseded and disputed facts.",
        enum=["current", "history"],
    ),
    limit=IntegerSchema(
        description="Maximum facts to return (default 8).",
        minimum=1,
        maximum=20,
    ),
    required=["query"],
))
class MemorySearchTool(_MemoryTool):
    """Search stored facts and past evidence."""

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory by meaning, keywords, scope and lifecycle state. "
            "Use it when the user asks about earlier decisions, preferences, projects or "
            "anything you may have recorded. Results include fact ids, current status, "
            "time validity and evidence references."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        scope: str | None = None,
        kind: str | None = None,
        status: str = "current",
        limit: int = 8,
        **kwargs: Any,
    ) -> str:
        statuses = (
            (FactStatus.ACTIVE,) if status != "history"
            else (FactStatus.ACTIVE, FactStatus.DORMANT, FactStatus.SUPERSEDED, FactStatus.DISPUTED)
        )
        kinds = (FactKind(kind),) if kind else ()
        try:
            hits = await self._memory.search(
                query,
                scope=scope or self._scope(),
                kinds=kinds,
                statuses=statuses,
                limit=limit,
            )
        except MemoryError as exc:
            return ToolResult.error(f"Error: {exc}")
        episodes = []
        match = fts_match_query(query)
        if match is not None:
            episodes = [
                {
                    "evidence_id": item.id,
                    "run_id": item.run_id,
                    "role": item.role,
                    "observed_at": item.observed_at,
                    "excerpt": " ".join(item.content.split())[:300],
                }
                for item in self._memory.store.search_evidence(match, 3)
            ]
        run_evidence: list[dict[str, Any]] = []
        run_id = self._context.turn_id if self._context is not None else None
        if run_id:
            run_evidence = [
                {
                    "evidence_id": item.id,
                    "tool_name": item.tool_name,
                    "observed_at": item.observed_at,
                    "excerpt": " ".join(item.content.split())[:300],
                }
                for item in self._memory.store.evidence_for_run(run_id)
                if item.kind is EvidenceKind.TOOL_RESULT and not item.suppressed
            ][-5:]
        return self._json({
            "facts": [hit.to_dict() for hit in hits],
            "episodes": episodes,
            "current_run_evidence": run_evidence,
            "semantic_index": self._memory.embedder is not None,
        })


@tool_parameters(tool_parameters_schema(
    fact_id=StringSchema("Fact id from memory_search."),
    required=["fact_id"],
))
class MemoryGetTool(_MemoryTool):
    """Read one fact with its history and evidence."""

    @property
    def name(self) -> str:
        return "memory_get"

    @property
    def description(self) -> str:
        return (
            "Read one memory fact in full: value, status, version history and the "
            "evidence that supports it. Use it after memory_search when the user asks "
            "why something is remembered or which version is current."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, fact_id: str, **kwargs: Any) -> str:
        detail = self._memory.fact_detail(fact_id)
        if detail is None:
            return ToolResult.error(f"Error: unknown fact {fact_id!r}")
        return self._json(detail)


@tool_parameters(tool_parameters_schema(
    subject=StringSchema("Stable subject key, e.g. 'user' or 'project:yoreni'."),
    predicate=StringSchema("Lowercase snake_case property, e.g. 'preferred_language'."),
    text=StringSchema("One self-contained sentence stating the fact."),
    kind=StringSchema("Memory kind.", enum=_KIND_VALUES),
    scope=StringSchema("'global' or 'project:<name>'. Defaults to global."),
    evidence=ArraySchema(
        StringSchema("Evidence id from this Run."),
        description=(
            "Evidence supporting the fact. Use current_run_evidence from "
            "memory_search for a tool result, or omit to cite the user's message."
        ),
        max_items=20,
        nullable=True,
    ),
    required=["subject", "predicate", "text", "kind"],
))
class MemoryRememberTool(_MemoryTool):
    """Explicitly store a fact the user asked to remember."""

    @property
    def name(self) -> str:
        return "memory_remember"

    @property
    def description(self) -> str:
        return (
            "Save a durable fact, preference or decision that the user explicitly asked "
            "you to remember, or that clearly must persist. Pass `evidence` with the "
            "tool result (from memory_search.current_run_evidence) when the fact came "
            "from a tool; omit it only when the user's own message supports the fact. "
            "A user request alone cannot establish an external fact. "
            "It is reconciled against existing facts."
        )

    async def execute(
        self,
        subject: str,
        predicate: str,
        text: str,
        kind: str,
        scope: str = "global",
        evidence: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            fact_ids = await self._memory.remember(
                run_id=self._run_id(),
                subject=subject,
                predicate=predicate,
                text=text,
                kind=FactKind(kind),
                scope=scope,
                evidence_ids=evidence,
            )
        except (MemoryError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")
        return self._json({"stored": fact_ids, "status": "active"})


@tool_parameters(tool_parameters_schema(
    fact_id=StringSchema("Fact id being corrected."),
    text=StringSchema("The corrected statement, self-contained."),
    kind=StringSchema("Optional new kind.", enum=_KIND_VALUES),
    subject=StringSchema("Optional new subject."),
    predicate=StringSchema("Optional new predicate."),
    scope=StringSchema("Optional new scope."),
    evidence=ArraySchema(
        StringSchema("Evidence id from this Run."),
        description="Evidence for the correction; defaults to the user's message.",
        max_items=20,
        nullable=True,
    ),
    required=["fact_id", "text"],
))
class MemoryCorrectTool(_MemoryTool):
    """Replace a fact with the user's correction, keeping its history."""

    @property
    def name(self) -> str:
        return "memory_correct"

    @property
    def description(self) -> str:
        return (
            "Correct a stored fact after the user states it changed or was wrong. The "
            "previous version stays in history as superseded; the correction becomes "
            "current. Pass `evidence` when a verified tool result established the new "
            "value; otherwise the user's message is the source."
        )

    async def execute(
        self,
        fact_id: str,
        text: str,
        kind: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        scope: str | None = None,
        evidence: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            new_id = await self._memory.correct(
                run_id=self._run_id(),
                fact_id=fact_id,
                text=text,
                kind=FactKind(kind) if kind else None,
                subject=subject,
                predicate=predicate,
                scope=scope,
                evidence_ids=evidence,
            )
        except (MemoryError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")
        return self._json({"fact_id": new_id, "supersedes": fact_id, "status": "active"})


@tool_parameters(tool_parameters_schema(
    fact_id=StringSchema("Fact id to delete."),
    query=StringSchema("Delete facts matching this description instead of an id."),
    limit=IntegerSchema(
        description="Maximum matching facts to delete when using query (default 5).",
        minimum=1,
        maximum=20,
    ),
))
class MemoryForgetTool(_MemoryTool):
    """Delete facts and their source text at the user's request."""

    @property
    def name(self) -> str:
        return "memory_forget"

    @property
    def description(self) -> str:
        return (
            "Permanently delete a fact when the user explicitly asks to forget it. "
            "This removes the fact, its search indexes and the source text that only "
            "supported it. Report clearly what was removed and that older local Git "
            "versions of core summaries may still exist."
        )

    async def execute(
        self,
        fact_id: str | None = None,
        query: str | None = None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        try:
            result = await self._memory.forget(
                run_id=self._run_id(),
                fact_id=fact_id,
                query=query,
                limit=limit,
            )
        except MemoryError as exc:
            return ToolResult.error(f"Error: {exc}")
        if not result["deleted"]:
            return self._json({"deleted": [], "note": "no matching memory was found"})
        logger.info("memory_forget removed {} fact(s)", len(result["deleted"]))
        return self._json(result)


__all__ = [
    "MemoryCorrectTool",
    "MemoryForgetTool",
    "MemoryGetTool",
    "MemoryRememberTool",
    "MemorySearchTool",
]
