"""Helpers for tests that exercise the Agent/Event execution seam."""

from __future__ import annotations

import asyncio
import faulthandler

from yoreni.agent.loop import AgentLoop
from yoreni.bus.events import InboundMessage
from yoreni.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RUNTIME_CONTEXT_MESSAGE_META,
    public_history_message,
)


def visible_message(message: dict) -> dict:
    marker = message.get("_meta", {}).get(RUNTIME_CONTEXT_MESSAGE_META)
    if marker is not None:
        message = {**message, RUNTIME_CONTEXT_HISTORY_META: marker}
    return public_history_message(message)


async def run_event(loop: AgentLoop, msg: InboundMessage) -> None:
    run_id = await loop.accept_message(msg)
    if run_id:
        while run_id in loop._accepted_runs:
            await asyncio.sleep(0)


async def stop_loop(loop: AgentLoop, worker: asyncio.Task[None], *, timeout: float) -> None:
    loop.stop()
    try:
        await asyncio.wait_for(worker, timeout=timeout)
    except TimeoutError:
        faulthandler.dump_traceback()
        raise
