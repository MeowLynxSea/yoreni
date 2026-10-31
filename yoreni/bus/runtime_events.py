"""Runtime state facts and turn-scoped publication through MessageBus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from yoreni.bus.events import InboundMessage
from yoreni.events import AgentEvent
from yoreni.providers.base import LLMUsage

if TYPE_CHECKING:
    from yoreni.bus.queue import MessageBus
    from yoreni.utils.llm_runtime import LLMRuntime


@dataclass(frozen=True)
class RuntimeEventContext:
    """Routing context common to turn-scoped runtime events."""

    channel: str
    chat_id: str
    run_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunStarted(AgentEvent):
    """A user/system turn has started its Run and is about to build context."""

    context: RuntimeEventContext


@dataclass(frozen=True)
class UserInputAccepted(AgentEvent):
    """User input was accepted for dispatch or injection into a Run."""

    context: RuntimeEventContext
    content: str
    message_id: str | None = None
    media: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnRuntimeAdmitted(AgentEvent):
    """The immutable model runtime selected for one admitted turn."""

    context: RuntimeEventContext
    runtime: LLMRuntime


@dataclass(frozen=True)
class TurnRunStatusChanged(AgentEvent):
    """Visible run status changed for a turn."""

    context: RuntimeEventContext
    status: str
    started_at: float | None = None


@dataclass(frozen=True)
class TurnCompleted(AgentEvent):
    """A turn has delivered its final user-visible response."""

    context: RuntimeEventContext
    latency_ms: int | None = None
    runtime: LLMRuntime | None = None
    usage: LLMUsage | None = None
    # Logical model rounds in display order; recovery dispatches are aggregated.
    round_usages: tuple[LLMUsage, ...] = ()
    outcome: str = "completed"
    failure_kind: str | None = None
    failure_error_kind: str | None = None
    failure_attempts: int | None = None


@dataclass(frozen=True)
class RunPersisted(AgentEvent):
    """A completed turn has been written to Run storage."""

    context: RuntimeEventContext
    turn_id: str
    sender_id: str


@dataclass(frozen=True)
class GoalStateChanged(AgentEvent):
    """The Agent's sustained-goal state changed, regardless of its trigger."""

    agent_id: str
    state: dict[str, Any]
    run_id: str | None = None


@dataclass(frozen=True)
class SubagentTaskChanged(AgentEvent):
    """A parent-owned task observation changed."""

    context: RuntimeEventContext
    task_id: str


@dataclass(frozen=True)
class RuntimeModelChanged(AgentEvent):
    """The active runtime model/preset changed."""

    model: str
    model_preset: str | None


class RuntimeEventPublisher:
    """Convenience publisher for turn-scoped runtime events.

    Agent code should decide when state transitions happen; this helper owns
    the mechanics of building event contexts and carrying per-turn metadata.
    """

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self._turn_latency_ms: dict[str, int] = {}
        self._turn_runtime: dict[str, LLMRuntime] = {}
        self._turn_usage: dict[str, LLMUsage] = {}
        self._turn_round_usages: dict[str, tuple[LLMUsage, ...]] = {}

    @staticmethod
    def _context(
        *,
        channel: str,
        chat_id: str,
        run_id: str,
        metadata: dict[str, Any] | None,
        attributes: dict[str, Any] | None = None,
    ) -> RuntimeEventContext:
        return RuntimeEventContext(
            channel=channel,
            chat_id=chat_id,
            run_id=run_id,
            metadata=dict(metadata or {}),
            attributes=dict(attributes or {}),
        )

    def record_turn_runtime(self, run_id: str, runtime: LLMRuntime) -> None:
        self._turn_runtime[run_id] = runtime

    def record_turn_latency(self, run_id: str, latency_ms: int | None) -> None:
        if latency_ms is not None:
            self._turn_latency_ms[run_id] = int(latency_ms)

    def record_turn_usage(
        self,
        run_id: str,
        round_usages: list[LLMUsage],
    ) -> None:
        if not round_usages:
            return

        usage = round_usages[0]
        for round_usage in round_usages[1:]:
            usage += round_usage
        previous = self._turn_usage.get(run_id)
        self._turn_usage[run_id] = usage if previous is None else previous + usage
        self._turn_round_usages[run_id] = (
            *self._turn_round_usages.get(run_id, ()),
            *round_usages,
        )

    def clear_turn(self, run_id: str) -> None:
        self._turn_latency_ms.pop(run_id, None)
        self._turn_runtime.pop(run_id, None)
        self._turn_usage.pop(run_id, None)
        self._turn_round_usages.pop(run_id, None)

    async def user_input_accepted(
        self,
        msg: InboundMessage,
        run_id: str,
        *,
        message_id: str | None = None,
    ) -> None:
        await self.bus.publish(
            UserInputAccepted(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    run_id=run_id,
                    metadata=msg.metadata,
                ),
                content=msg.content,
                message_id=message_id,
                media=tuple(msg.media),
            )
        )

    async def run_started(
        self,
        msg: InboundMessage,
        run_id: str,
    ) -> None:
        await self.bus.publish(
            RunStarted(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    run_id=run_id,
                    metadata=msg.metadata,
                ),
            )
        )

    async def turn_runtime_admitted(
        self,
        msg: InboundMessage,
        run_id: str,
        runtime: LLMRuntime,
    ) -> None:
        """Record and publish the runtime selected for one turn."""
        self.record_turn_runtime(run_id, runtime)
        await self.bus.publish(
            TurnRuntimeAdmitted(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    run_id=run_id,
                    metadata=msg.metadata,
                ),
                runtime=runtime,
            )
        )

    async def run_status_changed(
        self,
        msg: InboundMessage,
        run_id: str,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        await self.bus.publish(
            TurnRunStatusChanged(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    run_id=run_id,
                    metadata=msg.metadata,
                ),
                status=status,
                started_at=started_at,
            )
        )

    async def run_persisted(
        self,
        msg: InboundMessage,
        run_id: str,
        *,
        turn_id: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.bus.publish(
            RunPersisted(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    run_id=run_id,
                    metadata=msg.metadata,
                    attributes=attributes,
                ),
                turn_id=turn_id,
                sender_id=msg.sender_id,
            )
        )

    async def turn_completed(
        self,
        *,
        channel: str,
        chat_id: str,
        run_id: str,
        metadata: dict[str, Any] | None,
        outcome: str = "completed",
        failure_kind: str | None = None,
        failure_error_kind: str | None = None,
        failure_attempts: int | None = None,
    ) -> None:
        await self.bus.publish(
            TurnCompleted(
                context=self._context(
                    channel=channel,
                    chat_id=chat_id,
                    run_id=run_id,
                    metadata=metadata,
                ),
                latency_ms=self._turn_latency_ms.pop(run_id, None),
                runtime=self._turn_runtime.pop(run_id, None),
                usage=self._turn_usage.pop(run_id, None),
                round_usages=self._turn_round_usages.pop(run_id, ()),
                outcome=outcome,
                failure_kind=failure_kind,
                failure_error_kind=failure_error_kind,
                failure_attempts=failure_attempts,
            )
        )

    def runtime_model_changed(self, model: str, model_preset: str | None) -> None:
        self.bus.publish_nowait(
            RuntimeModelChanged(model=model, model_preset=model_preset)
        )
