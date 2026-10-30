"""Messages the owner sends into a turn that is already running."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from yoreni.agent.state import AgentInput

# The newest owner message this turn knows about. A message that broke into a
# running turn becomes the default target for replies and reactions.
_CURRENT_MESSAGE_ID: ContextVar[str | None] = ContextVar(
    "current_user_message_id",
    default=None,
)


def current_user_message_id() -> str | None:
    """Return the newest owner message ID for this turn, if any."""
    return _CURRENT_MESSAGE_ID.get()


def _render_injected(event: AgentInput) -> dict[str, Any]:
    """Describe an interruption to the model without hiding who spoke."""
    lines = [
        "[The user sent this while you were still working. Stop the current step, "
        "react to this message, and handle it.]",
        f"Message ID: {event.event_id}",
    ]
    if event.content.strip():
        lines.append(event.content)
    if event.media:
        lines.append("Attachments: " + ", ".join(event.media))
    return {"role": "user", "content": "\n".join(lines)}


@dataclass
class TurnInjections:
    """One running turn's inbox for messages that broke into it."""

    pending: asyncio.Queue[AgentInput] = field(default_factory=asyncio.Queue)
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    accepting: bool = True

    def try_push(self, event: AgentInput) -> bool:
        """Queue one input and wake any tool call that is still running.

        Returns False once the turn stopped consuming input, so the caller can
        start a normal Run instead of writing into a finished turn.
        """
        if not self.accepting:
            return False
        self.pending.put_nowait(event)
        self.interrupt.set()
        return True

    def close(self) -> list[AgentInput]:
        """Stop accepting input and return what never reached the model."""
        self.accepting = False
        leftovers: list[AgentInput] = []
        while not self.pending.empty():
            leftovers.append(self.pending.get_nowait())
        self.interrupt.set()
        return leftovers

    async def drain(self) -> list[dict[str, Any]]:
        """Take the current snapshot and let the turn continue uninterrupted."""
        events: list[AgentInput] = []
        while not self.pending.empty():
            events.append(self.pending.get_nowait())
        self.interrupt.clear()
        if events:
            _CURRENT_MESSAGE_ID.set(events[-1].event_id)
        return [_render_injected(event) for event in events]

    async def wait(self) -> None:
        """Wait until a new input arrives."""
        await self.interrupt.wait()
