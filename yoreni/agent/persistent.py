"""Application execution for the persistent personal agent."""

# Goal schemas are supplied by the existing tool_parameters class decorator.
# The coordinator uses the loop's established execution boundary.
# pyright: reportPrivateUsage=false, reportAbstractUsage=false

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from filelock import FileLock, Timeout

from yoreni.agent.agent_state_tool import AgentStateTool
from yoreni.agent.context import TranscriptInput
from yoreni.agent.goal_permission import GoalInputScope
from yoreni.agent.goal_state import goal_state_runtime_lines
from yoreni.agent.injection import TurnInjections
from yoreni.agent.recovery import checkpoint_history
from yoreni.agent.runner import RequiredToolRound
from yoreni.agent.state import (
    AgentInput,
    AgentStore,
    PendingRun,
    RunOutcome,
    StoredRun,
)
from yoreni.agent.tools.base import Tool, ToolResult
from yoreni.agent.tools.context import ContextAware, RequestContext
from yoreni.agent.tools.file_state import FileStates
from yoreni.agent.tools.send_message import capture_message_deliveries
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.bus.events import INBOUND_META_TURN_ID, InboundMessage, OutboundMessage
from yoreni.bus.outbound_events import StreamDeltaEvent, StreamEndEvent
from yoreni.cron.events import cron_run_id
from yoreni.events import AgentEvent, EventSink
from yoreni.runtime_context import (
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    append_runtime_context,
    public_history_messages,
    wrap_runtime_context_lines,
)
from yoreni.triggers.events import local_trigger_delivery_id
from yoreni.utils.document import reference_non_image_attachments
from yoreni.utils.progress_events import output_events

_REACTION_ROUND = RequiredToolRound(
    tool_name="react",
    instruction=(
        "React to the message the user just sent with the react tool before "
        "answering. One emoji that shows your first reaction, or NONE when no "
        "emoji fits it."
    ),
)


if TYPE_CHECKING:
    from yoreni.agent.hook import AgentHook
    from yoreni.agent.loop import AgentLoop
    from yoreni.agent.turn_delivery import TurnDelivery
    from yoreni.utils.llm_runtime import LLMRuntime



@asynccontextmanager
async def resource_lock(store: AgentStore, resource: str) -> AsyncGenerator[None]:
    """Use process-safe leases without blocking the gateway event loop."""
    path = store.path.parent / ".agent-locks"
    path.mkdir(exist_ok=True)
    lock = FileLock(path / f"{hashlib.sha256(resource.encode()).hexdigest()}.lock")
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def recoverable_runs(store: AgentStore) -> list[PendingRun]:
    """Exclude live executions when presenting durable recovery choices."""
    result: list[PendingRun] = []
    for run in store.pending():
        resource = f"event:{run['id']}"
        path = store.path.parent / ".agent-locks" / f"{hashlib.sha256(resource.encode()).hexdigest()}.lock"
        path.parent.mkdir(exist_ok=True)
        lock = FileLock(path)
        try:
            with lock.acquire(timeout=0):
                result.append(run)
        except Timeout:
            pass
    return result


def memory_snapshot(store: AgentStore) -> dict[str, str]:
    root = store.path.parent
    return {
        str((root / name).resolve()): hashlib.sha256((root / name).read_bytes()).hexdigest()
        if (root / name).exists() else ""
        for name in ("SOUL.md", "USER.md", "memory/MEMORY.md")
    }


class _CoordinatedTool(Tool):
    """Coordinate effects between runs, retaining the original tool contracts."""

    def __init__(self, tool: Tool, store: AgentStore, snapshots: dict[str, str]):
        self.tool = tool
        self.store = store
        self.snapshots = snapshots
        self._legacy_context: RequestContext | None = None

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def description(self) -> str:
        return self.tool.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.tool.parameters

    @property
    def read_only(self) -> bool:
        return self.tool.read_only

    @property
    def exclusive(self) -> bool:
        return self.tool.exclusive

    @property
    def concurrency_safe(self) -> bool:
        return self.tool.concurrency_safe

    @property
    def interruptible(self) -> bool:
        return self.tool.interruptible

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.tool.cast_params(params)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return self.tool.validate_params(params)

    def runtime_context_provider(self):
        return self.tool.runtime_context_provider()

    def set_context(self, ctx: RequestContext) -> None:
        self._legacy_context = ctx

    async def _invoke(self, kwargs: dict[str, Any]) -> Any:
        if isinstance(self.tool, ContextAware) and self._legacy_context is not None:
            async with resource_lock(self.store, f"legacy-tool:{self.name}"):
                self.tool.set_context(self._legacy_context)
                return await self.tool.execute(**kwargs)
        return await self.tool.execute(**kwargs)

    async def execute(self, **kwargs: Any) -> Any:
        if self.read_only:
            if self.name == "read_file":
                from pathlib import Path

                path = kwargs.get("path")
                if isinstance(path, str):
                    candidate = Path(path).expanduser()
                    if not candidate.is_absolute():
                        from yoreni.agent.tools.context import current_request_context

                        context = current_request_context()
                        candidate = (context.workspace if context and context.workspace else self.store.path.parent) / candidate
                    resolved = str(candidate.resolve())
                    if resolved in self.snapshots:
                        async with resource_lock(self.store, "tool-effects"):
                            result = await self._invoke(kwargs)
                            if not getattr(result, "is_error", False):
                                self.snapshots[resolved] = memory_snapshot(self.store)[resolved]
                            return result
            return await self._invoke(kwargs)
        async with resource_lock(self.store, "tool-effects"):
            current = memory_snapshot(self.store)
            # Shell/MCP can edit arbitrary files. Their effects share this lease;
            # reject stale memory before allowing a mutation from an old context.
            if current != self.snapshots:
                return ToolResult.error(
                    "Agent memory changed during this run. Read SOUL.md, USER.md and "
                    "memory/MEMORY.md again and merge your changes before writing."
                )
            result = await self._invoke(kwargs)
            self.snapshots.update(memory_snapshot(self.store))
            return result


class PersistentAgent:
    """One durable identity with independent runs and a bounded context selection."""

    def __init__(self, loop: AgentLoop):
        self.loop = loop
        self._store: AgentStore | None = None

    @property
    def store(self) -> AgentStore:
        if self._store is None:
            self._store = AgentStore(self.loop.workspace)
        return self._store

    @staticmethod
    def input_from_message(msg: InboundMessage) -> AgentInput:
        correlation = msg.metadata.get(INBOUND_META_TURN_ID)
        cron_id = cron_run_id(msg.metadata)
        trigger_id = local_trigger_delivery_id(msg.metadata)
        source = "cron" if cron_id else "local_trigger" if trigger_id else msg.channel
        metadata = dict(msg.metadata)
        if cron_id or trigger_id:
            # Retry envelopes have fresh delivery turn IDs; the event identity
            # belongs to the durable cron run / trigger delivery instead.
            metadata[INBOUND_META_TURN_ID] = cron_id or trigger_id
        return AgentInput(
            source=source, content=msg.content, principal="owner", channel=msg.channel, chat_id=msg.chat_id,
            kind="internal" if cron_id else "external" if trigger_id else "user",
            event_id=cron_id or trigger_id or (correlation if isinstance(correlation, str) and correlation else str(msg.timestamp.timestamp())),
            media=tuple(msg.media),
            metadata={**metadata, "sender_id": msg.sender_id},
        )

    async def archive_pending(self) -> None:
        """Drain the durable interaction outbox into the existing Dream journal."""
        for run_id, messages in await asyncio.to_thread(self.store.pending_journal):
            visible = [row for row in public_history_messages(messages)
                       if row.get("role") in {"user", "assistant"} and not row.get("tool_calls")]
            entry = self.loop.context.memory._format_messages(visible)
            await asyncio.to_thread(
                self.loop.context.memory.append_history, entry, entry_id=run_id, max_chars=8000,
            )
            await asyncio.to_thread(self.store.mark_journaled, run_id)

    async def execute(
        self, event: AgentInput, *,
        execution_id: str,
        resume: bool = False,
        runtime: LLMRuntime | None = None,
        tools: ToolRegistry | None = None,
        hooks: list[AgentHook] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_runtime_admitted: Callable[[LLMRuntime], Awaitable[None]] | None = None,
        attributes: dict[str, Any] | None = None,
        delivery: TurnDelivery | None = None,
        injections: TurnInjections | None = None,
        stream_answers: bool = True,
    ) -> OutboundMessage:
        if event.principal != "owner":
            raise PermissionError("This event has no access to the personal agent.")
        if event.ephemeral:
            if resume:
                raise ValueError("Ephemeral Runs have no durable recovery checkpoint.")
            record: StoredRun = {
                "id": execution_id, "event_seq": 0, "status": "queued", "error": None,
                "transcript": [], "checkpoint": None, "result": None, "journaled": 0,
                "payload": {},
            }
            try:
                return await self._execute(
                    event, record["id"], record, runtime=runtime, tools=tools, hooks=hooks,
                    on_progress=on_progress, on_stream=on_stream,
                    on_stream_end=on_stream_end, delivery=delivery,
                    on_runtime_admitted=on_runtime_admitted, attributes=attributes,
                    injections=injections, stream_answers=stream_answers,
                )
            except asyncio.CancelledError:
                identity = await asyncio.to_thread(self.store.identity)
                await self.loop._exec_session_manager.terminate_by_owner(
                    f"agent:{identity.id}:run:{record['id']}",
                )
                raise
        run_id = await asyncio.to_thread(self.store.admit, event)
        # User interactions share recent context; background work has its own
        # lane and acquires resource leases only while executing effects.
        lane = "interaction" if event.kind == "user" else f"run:{run_id}"
        async with resource_lock(self.store, f"event:{run_id}"):
            async with resource_lock(self.store, lane):
                record = await asyncio.to_thread(self.store.read_run, run_id)
                if record["result"] is not None:
                    await self.archive_pending()
                    return self._outbound(event, run_id, record["result"])
                if record["status"] != "queued" and not resume:
                    raise RuntimeError(f"Run {run_id} requires explicit recovery.")
                try:
                    response = await self._execute(
                        event, run_id, record, runtime=runtime, tools=tools, hooks=hooks,
                        on_progress=on_progress, on_stream=on_stream,
                        on_stream_end=on_stream_end, delivery=delivery,
                        on_runtime_admitted=on_runtime_admitted, attributes=attributes,
                        injections=injections, stream_answers=stream_answers,
                    )
                    await self.archive_pending()
                    return response
                except asyncio.CancelledError:
                    identity = await asyncio.to_thread(self.store.identity)
                    await self.loop._exec_session_manager.terminate_by_owner(f"agent:{identity.id}:run:{run_id}")
                    await asyncio.to_thread(
                        self.store.interrupt, run_id, "Execution cancelled",
                        cancelled=not self.loop._preserve_inflight_turns_on_shutdown,
                    )
                    raise
                except Exception as exc:
                    await asyncio.to_thread(self.store.interrupt, run_id, str(exc))
                    raise

    async def _execute(
        self, event: AgentInput, run_id: str, record: StoredRun, *,
        runtime: LLMRuntime | None, tools: ToolRegistry | None,
        hooks: list[AgentHook] | None,
        on_progress: Callable[..., Awaitable[None]] | None,
        on_stream: Callable[[str], Awaitable[None]] | None,
        on_stream_end: Callable[..., Awaitable[None]] | None,
        delivery: TurnDelivery | None,
        on_runtime_admitted: Callable[[LLMRuntime], Awaitable[None]] | None,
        attributes: dict[str, Any] | None,
        injections: TurnInjections | None,
        stream_answers: bool,
    ) -> OutboundMessage:
        loop = self.loop
        identity = await asyncio.to_thread(self.store.identity)
        checkpoint = record["checkpoint"]
        if runtime is None:
            preset = identity.state.get("model_preset")
            runtime = (
                loop.runtime_resolver.resolve_preset(preset)
                if isinstance(preset, str) and preset in loop.model_presets else loop.llm_runtime()
            )
        if on_runtime_admitted is not None:
            await on_runtime_admitted(runtime)
        history = await asyncio.to_thread(self.store.recent) if event.kind == "user" and not event.ephemeral else []
        history = public_history_messages(history)
        context_count = len(history)
        content, media = reference_non_image_attachments(event.content, list(event.media))
        blocks = [RuntimeContextBlock(
            source="agent_state",
            content=wrap_runtime_context_lines([
                "Persistent agent identity and state (JSON data):",
                json.dumps({
                    "agent_id": identity.id, "revision": identity.revision, "state": identity.state,
                    "event": {"source": event.source, "kind": event.kind, "id": event.event_id},
                }, ensure_ascii=False),
                "Use agent_state to maintain preferences and task progress across runs.",
            ]),
        )]
        if event.kind == "user" and not event.ephemeral:
            blocks.append(await self._conversation_block())
        current_message: str | None = content
        if checkpoint:
            history = public_history_messages(checkpoint["messages"][1:])
            context_count = checkpoint["context_count"]
            history.extend(
                message for message in checkpoint_history(checkpoint)
                if message.get("_recovery_interrupted")
            )
            if checkpoint["phase"] == "continuation":
                history.extend(checkpoint["continuation_messages"])
            current_message = None
            # A durable final response needs delivery, not another model call.
            if checkpoint["phase"] == "final_response":
                messages = history[context_count:]
                saved_outcome: RunOutcome = {"content": checkpoint["assistant_message"]["content"], "stop_reason": "completed"}
                await asyncio.to_thread(
                    self.store.finish, run_id, messages, saved_outcome,
                )
                return self._outbound(event, run_id, saved_outcome)
        if not event.ephemeral:
            await asyncio.to_thread(self.store.start, run_id, history)
        scope = loop.workspace_scopes.for_turn(
            channel=event.channel, message_metadata=event.metadata,
        )
        request = RequestContext(
            channel=event.channel, chat_id=event.chat_id, sender_id=event.principal,
            message_id=event.event_id if event.kind == "user" else None,
            turn_id=run_id, runtime=runtime, workspace=scope.project_path, workspace_scope=scope,
            original_user_text=event.content if event.kind == "user" else None,
            metadata={**event.metadata, "_agent_event_source": event.source},
            attributes={**(attributes or {}), "agent_id": identity.id, "run_id": run_id, "event_id": event.event_id},
            agent_id=identity.id,
            can_receive_background_results=False,
            log_content=not event.ephemeral,
        )
        registry = ToolRegistry()
        # Run-local receipts and shared effect leases prevent unrelated runs from
        # inheriting each other's file-read deduplication or mutation baselines.
        source_tools = tools if tools is not None else loop.tools
        snapshots = (
            dict(checkpoint["memory_snapshot"])
            if checkpoint and "memory_snapshot" in checkpoint else memory_snapshot(self.store)
        )
        for name in source_tools.tool_names:
            tool = source_tools.get(name)
            if tool is not None:
                registry.register(_CoordinatedTool(tool, self.store, snapshots))
        if tools is None:
            registry.register(AgentStateTool(self.store))
        if event.kind == "user":
            blocks.extend(await loop._resolve_runtime_context_for_request(request, registry))
        if checkpoint:
            # Other runs may update preferences/goals while this run is stopped.
            # Replace the old runtime suffix without disturbing tool-call order.
            for message in reversed(history):
                if message["role"] == "user":
                    message["content"], marker = append_runtime_context(message["content"], blocks)
                    message["_meta"] = {**message.get("_meta", {}), RUNTIME_CONTEXT_MESSAGE_META: marker}
                    break
        events = output_events(
            default=delivery.events if delivery else loop.turn_delivery_factory.unrouted(
                InboundMessage(event.channel, event.principal, event.chat_id, event.content), run_id,
            ).events,
            on_progress=on_progress, on_stream=on_stream, on_stream_end=on_stream_end,
        )
        if delivery is not None:
            await delivery.runtime_admitted(runtime)
            await delivery.running(started_at=time.time())

        async def save_checkpoint(payload: dict[str, Any]) -> None:
            portable = {key: value for key, value in payload.items() if key != "provider_state"}
            portable["context_count"] = context_count
            portable["memory_snapshot"] = dict(snapshots)
            if not event.ephemeral:
                await asyncio.to_thread(self.store.checkpoint, run_id, portable)

        async def goal_continuation() -> str | None:
            if event.kind != "user":
                return None
            state = await asyncio.to_thread(self.store.identity)
            lines = goal_state_runtime_lines(state.state)
            if not lines:
                return None
            return (
                "Continue the active sustained goal using the completed tool results:\n"
                + "\n".join(lines)
                + "\nCall update_goal with action='complete' only when the objective is achieved."
            )

        streamed = False
        segment_streamed = False
        publish = events.publish

        async def track_output(output: AgentEvent) -> None:
            nonlocal streamed, segment_streamed
            if isinstance(output, StreamDeltaEvent) and output.content:
                segment_streamed = True
            elif isinstance(output, StreamEndEvent):
                streamed = segment_streamed
                segment_streamed = False
            if publish is not None:
                await publish(output)

        events = EventSink(track_output, events.accepts)
        result = await loop._run_agent_loop(
            TranscriptInput(
                history=history, current_message=current_message,
                media=media, runtime_context_blocks=blocks,
            ),
            runtime=runtime, request_context=request, tools=registry, hooks=hooks,
            ephemeral=event.ephemeral, run_extra_hooks_for_ephemeral=True,
            events=events, streaming=on_stream is not None or (delivery is not None and delivery.streaming),
            checkpoint_callback=None if event.ephemeral else save_checkpoint, file_states=FileStates(),
            continuation_callback=goal_continuation,
            required_tool_round=_REACTION_ROUND if injections is not None and event.kind == "user" else None,
            interrupt=injections.interrupt if injections is not None else None,
            injections=injections,
            stream_answers=stream_answers,
            turn_scopes=[GoalInputScope(InboundMessage(
                event.channel, event.principal, event.chat_id, event.content,
                input_role="user" if event.kind == "user" else "system", metadata=event.metadata,
            ))],
        )
        messages = result.messages[1 + context_count:]
        outcome: RunOutcome = {"content": result.final_content or "", "stop_reason": result.stop_reason}
        if not event.ephemeral:
            await asyncio.to_thread(
                self.store.finish, run_id, messages, outcome,
            )
        if delivery is not None:
            delivery.record_usage(result.round_usages)
            delivery.record_stop_reason(result.stop_reason, failure_error_kind=result.failure_error_kind)
        response = self._outbound(event, run_id, outcome)
        if delivery is not None:
            assembled = loop._assemble_outbound(
                delivery.delivery_message, response.content, result.stop_reason, streamed,
            )
            assert assembled is not None
            assembled.metadata.update(response.metadata)
            return assembled
        return response

    async def _conversation_block(self) -> RuntimeContextBlock:
        """Expose message IDs so replies can target one earlier message."""
        recent = await asyncio.to_thread(self.store.recent_user_messages, limit=6)
        rows = [
            {"id": str(row["id"]), "text": str(row["text"])[:200]}
            for row in recent
        ]
        return RuntimeContextBlock(
            source="conversation",
            content=wrap_runtime_context_lines([
                "Recent messages from the user, newest first. Pass one of these IDs as "
                "send_message.reply_to when a message answers that specific message.",
                json.dumps(rows, ensure_ascii=False),
            ]),
        )

    @staticmethod
    def _outbound(event: AgentInput, run_id: str, result: RunOutcome) -> OutboundMessage:
        return OutboundMessage(
            event.channel, event.chat_id, result["content"],
            metadata={"_stop_reason": result["stop_reason"], "agent_run_id": run_id},
        )

    async def submit_message(
        self,
        event: AgentInput,
        msg: InboundMessage,
        *,
        resume: bool = False,
        injections: TurnInjections | None = None,
    ) -> OutboundMessage | None:
        """Deliver a transport Event's Run without creating a conversation owner."""
        run_id = await asyncio.to_thread(self.store.admit, event)
        delivery = self.loop.turn_delivery_factory.create(msg, run_id, enable_stream=True)
        await delivery.started()
        try:
            with capture_message_deliveries() as sent:
                response = await self.loop.process_event(
                    event, delivery=delivery, resume=resume, injections=injections,
                    stream_answers=False,
                )
            # The agent speaks through send_message. The model's own text is a
            # fallback only when the turn produced no message at all, so a
            # legacy prompt or a failure never leaves the user with silence.
            if sent.count and (response is None or response.metadata.get("_stop_reason") != "error"):
                response = None
            await delivery.complete(response, publish_completion=True)
            return response
        except asyncio.CancelledError:
            await delivery.abort_stream()
            raise
        except Exception:
            await delivery.fail(publish_completion=True)
            raise
        finally:
            await delivery.idle()

    async def resume(self, run_id: str, *, deliver: bool = False) -> OutboundMessage | None:
        record = await asyncio.to_thread(self.store.read_run, run_id)
        payload = record["payload"]
        payload["media"] = tuple(payload["media"])
        event = AgentInput(**payload)
        if deliver:
            msg = InboundMessage(event.channel, event.principal, event.chat_id, event.content,
                                 media=list(event.media), metadata=event.metadata,
                                 input_role="user" if event.kind == "user" else "system")
            return await self.submit_message(event, msg, resume=True)
        return await self.loop.process_event(event, resume=True)
