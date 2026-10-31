"""Helpers shared by project tests (no pytest fixtures here)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from yoreni.agent.state import AgentInput, AgentStore
from yoreni.config.schema import MemoryConfig
from yoreni.memory.models import FactKind
from yoreni.memory.service import MemoryService
from yoreni.projects.models import Project
from yoreni.projects.scope import GLOBAL_SCOPE
from yoreni.projects.service import ProjectService


@dataclass(frozen=True)
class ProjectEnv:
    """One temporary workspace with the stores the gateway would build."""

    workspace: Path
    store: AgentStore
    memory: MemoryService
    projects: ProjectService

    def create(self, name: str, description: str = "", emoji: str = "") -> Project:
        return self.projects.create(name=name, description=description, emoji=emoji)

    def turn(
        self,
        content: str,
        *,
        scope: str = GLOBAL_SCOPE,
        message_id: str | None = None,
        answer: str = "noted",
    ) -> str:
        """Admit, run and finish one real turn in one conversation scope."""
        run_id = self.store.admit(AgentInput(
            source="webui",
            content=content,
            principal="owner",
            event_id=message_id or uuid4().hex,
            scope=scope,
        ))
        self.store.start(run_id, [{"role": "user", "content": content}])
        self.store.append_entry(run_id, "message", {
            "id": f"{run_id}:answer", "content": answer,
        })
        self.store.finish(
            run_id,
            [
                {"role": "user", "content": content},
                {"role": "assistant", "content": answer},
            ],
            {"content": answer, "stop_reason": "stop"},
        )
        return run_id

    async def fact(
        self,
        run_id: str,
        text: str,
        *,
        scope: str,
        subject: str = "user",
        predicate: str = "note",
    ) -> str:
        """Record one user statement as evidence and commit a fact from it."""
        self.memory.record_run(run_id, [{"role": "user", "content": text}], scope=scope)
        fact_ids = await self.memory.remember(
            run_id=run_id,
            subject=subject,
            predicate=predicate,
            text=text,
            kind=FactKind.USER_FACT,
            scope=scope,
        )
        return fact_ids[0]


def build_env(workspace: Path) -> ProjectEnv:
    store = AgentStore(workspace)
    memory = MemoryService(workspace, MemoryConfig())
    return ProjectEnv(
        workspace=workspace,
        store=store,
        memory=memory,
        projects=ProjectService(workspace, store, memory=memory),
    )
