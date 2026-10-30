"""Adapt Agent/Run facts to the live personal WebUI."""

import time
from contextlib import contextmanager

from yoreni.agent.goal_state import goal_state_ws_blob
from yoreni.agent.state import PERSONAL_CHAT_ID
from yoreni.agent.tools.context import current_request_context
from yoreni.bus.outbound_events import (
    GoalStateSyncEvent,
    GoalStatusEvent,
    RuntimeModelUpdatedEvent,
    TurnEndEvent,
    TurnModelUpdatedEvent,
    UserInputEvent,
    WidgetChangedEvent,
    outbound_message_for_event,
)
from yoreni.bus.queue import MessageBus
from yoreni.bus.runtime_events import (
    GoalStateChanged,
    RuntimeModelChanged,
    TurnCompleted,
    TurnRunStatusChanged,
    TurnRuntimeAdmitted,
    UserInputAccepted,
)
from yoreni.providers.fallback_provider import FallbackModelObserver, FallbackModelSelection
from yoreni.webui.home_widgets import remove_home_widget_entry
from yoreni.widgets.types import WidgetChange


async def publish_widget_change(bus: MessageBus, change: WidgetChange) -> None:
    """Push one widget change to the personal WebUI and forget deleted layouts."""
    if change.kind == "deleted":
        remove_home_widget_entry(change.widget_id)
    await bus.publish_outbound(outbound_message_for_event(
        channel="websocket", chat_id=PERSONAL_CHAT_ID,
        event=WidgetChangedEvent(change.widget_id, change.kind),
    ))


def build_webui_fallback_model_observer(bus: MessageBus) -> FallbackModelObserver:
    async def publish(selection: FallbackModelSelection) -> None:
        request = current_request_context()
        if request is not None and request.channel == "websocket":
            await bus.publish_outbound(outbound_message_for_event(
                channel="websocket", chat_id=PERSONAL_CHAT_ID,
                event=TurnModelUpdatedEvent(
                    model=selection.model, model_preset=request.runtime.model_preset if request.runtime else None,
                    fallback=True, reauth_provider=selection.reauth_provider,
                ), metadata=request.metadata,
            ))
    return publish


@contextmanager
def connect_run_events(bus: MessageBus):
    async def accepted(event: UserInputAccepted) -> None:
        if event.context.channel == "websocket":
            await bus.publish_outbound(outbound_message_for_event(
                channel="websocket", chat_id=PERSONAL_CHAT_ID,
                event=UserInputEvent(
                    event.content,
                    int(time.time() * 1000),
                    {},
                    message_id=event.message_id,
                ),
                metadata=event.context.metadata,
            ))

    async def status(event: TurnRunStatusChanged) -> None:
        if event.context.channel == "websocket":
            await bus.publish_outbound(outbound_message_for_event(
                channel="websocket", chat_id=PERSONAL_CHAT_ID,
                event=GoalStatusEvent(event.status, event.started_at), metadata=event.context.metadata,
            ))

    async def model(event: TurnRuntimeAdmitted) -> None:
        if event.context.channel == "websocket":
            await bus.publish_outbound(outbound_message_for_event(
                channel="websocket", chat_id=PERSONAL_CHAT_ID,
                event=TurnModelUpdatedEvent(event.runtime.model, event.runtime.model_preset, event.runtime.context_window_tokens),
                metadata=event.context.metadata,
            ))

    async def completed(event: TurnCompleted) -> None:
        if event.context.channel == "websocket":
            await bus.publish_outbound(outbound_message_for_event(
                channel="websocket", chat_id=PERSONAL_CHAT_ID,
                event=TurnEndEvent(
                    latency_ms=event.latency_ms, usage=event.usage, round_usages=event.round_usages,
                    outcome=event.outcome, failure_kind=event.failure_kind,
                    failure_error_kind=event.failure_error_kind, failure_attempts=event.failure_attempts,
                    context_window_tokens=event.runtime.context_window_tokens if event.runtime else None,
                ), metadata=event.context.metadata,
            ))

    async def goal(event: GoalStateChanged) -> None:
        await bus.publish_outbound(outbound_message_for_event(
            channel="websocket", chat_id=PERSONAL_CHAT_ID,
            event=GoalStateSyncEvent(goal_state_ws_blob(event.state)),
        ))

    async def runtime(event: RuntimeModelChanged) -> None:
        await bus.publish_outbound(outbound_message_for_event(
            channel="websocket", chat_id=PERSONAL_CHAT_ID,
            event=RuntimeModelUpdatedEvent(event.model, event.model_preset),
        ))

    unsubscribe = [
        bus.subscribe(accepted, UserInputAccepted),
        bus.subscribe(status, TurnRunStatusChanged), bus.subscribe(model, TurnRuntimeAdmitted),
        bus.subscribe(completed, TurnCompleted), bus.subscribe(goal, GoalStateChanged),
        bus.subscribe(runtime, RuntimeModelChanged),
    ]
    try:
        yield
    finally:
        for remove in unsubscribe:
            remove()
