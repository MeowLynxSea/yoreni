"""Tests for messages that break into a running turn: inbox, reaction round, interruption."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from yoreni.agent.injection import TurnInjections
from yoreni.agent.runner import AgentRunner, RequiredToolRound
from yoreni.agent.state import AgentInput
from yoreni.agent.tools.base import Tool
from yoreni.agent.tools.execution import execute_tool_calls
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.config.schema import AgentDefaults
from yoreni.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


class _NamedTool(Tool):
    def __init__(self, name: str, *, interruptible: bool = False, delay: float = 5.0) -> None:
        self._name = name
        self._delay = delay
        self.started = asyncio.Event()
        self.finished = False
        self.interruptible = interruptible

    @property
    def concurrency_safe(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        self.started.set()
        await asyncio.sleep(self._delay)
        self.finished = True
        return "finished"


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _input(content: str = "what about this?", *, event_id: str = "msg-2") -> AgentInput:
    return AgentInput(
        source="websocket", content=content, principal="owner", event_id=event_id,
        channel="websocket", chat_id="yoreni",
    )


@pytest.mark.asyncio
async def test_drain_renders_the_message_as_an_interruption() -> None:
    inbox = TurnInjections()
    assert inbox.try_push(AgentInput(
        source="websocket", content="also check the logs", principal="owner",
        event_id="msg-9", channel="websocket", chat_id="yoreni", media=("facts.txt",),
    ))

    messages = await inbox.drain()

    assert messages == [{
        "role": "user",
        "content": (
            "[The user sent this while you were still working. Stop the current step, "
            "react to this message, and handle it.]\n"
            "Message ID: msg-9\n"
            "also check the logs\n"
            "Attachments: facts.txt"
        ),
    }]
    assert not inbox.interrupt.is_set()


@pytest.mark.asyncio
async def test_closed_inbox_returns_what_never_reached_the_model() -> None:
    inbox = TurnInjections()
    first, second = _input("one", event_id="msg-1"), _input("two", event_id="msg-3")
    assert inbox.try_push(first)
    assert inbox.try_push(second)

    leftovers = inbox.close()

    assert [event.content for event in leftovers] == ["one", "two"]
    assert inbox.try_push(_input("late", event_id="msg-4")) is False
    assert await inbox.drain() == []


@pytest.mark.asyncio
async def test_reaction_round_offers_only_the_required_tool() -> None:
    provider = MagicMock()
    offered: list[list[str]] = []
    responses = [
        LLMResponse(content="on it", tool_calls=[], usage=None),
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="c1", name="react", arguments={"emoji": "👀"})],
            usage=None,
        ),
        LLMResponse(content="done", tool_calls=[], usage=None),
    ]

    async def respond(**kwargs: Any) -> LLMResponse:
        offered.append([tool["function"]["name"] for tool in kwargs.get("tools") or []])
        return responses.pop(0)

    provider.chat_stream_with_retry = AsyncMock(side_effect=respond)
    inbox = TurnInjections()
    inbox.try_push(_input())

    result = await AgentRunner().run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "start"}],
        tools=_registry(_NamedTool("react"), _NamedTool("echo")),
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inbox.drain,
        required_tool_round=RequiredToolRound("react", "React first."),
    ))

    assert result.final_content == "done"
    assert result.tools_used == ["react"]
    # The round after the interruption is restricted to the reaction tool; the
    # prose answer in that round is nudged instead of accepted. Once the model
    # reacts, the normal toolset comes back.
    assert [sorted(names) for names in offered] == [["react"], ["react"], ["echo", "react"]]
    nudged = [message["content"] for message in result.messages if message["role"] == "user"]
    assert "React first." in nudged


@pytest.mark.asyncio
async def test_reaction_round_stops_nudging_after_the_limit() -> None:
    provider = MagicMock()
    provider.chat_stream_with_retry = AsyncMock(side_effect=[
        LLMResponse(content="one", tool_calls=[], usage=None),
        LLMResponse(content="two", tool_calls=[], usage=None),
        LLMResponse(content="three", tool_calls=[], usage=None),
        LLMResponse(content="final", tool_calls=[], usage=None),
    ])
    inbox = TurnInjections()
    inbox.try_push(_input())

    result = await AgentRunner().run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "start"}],
        tools=_registry(_NamedTool("react")),
        model="test-model",
        max_iterations=6,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inbox.drain,
        required_tool_round=RequiredToolRound("react", "React first."),
    ))

    # Two nudges, then the turn is allowed to answer without reacting.
    assert result.final_content == "three"
    assert provider.chat_stream_with_retry.await_count == 3


@pytest.mark.asyncio
async def test_interrupt_abandons_a_running_interruptible_tool() -> None:
    interrupt = asyncio.Event()
    registry = _registry(_NamedTool("web_search", interruptible=True, delay=5.0))
    hook = MagicMock()
    hook.before_execute_tool = AsyncMock()
    hook.after_execute_tool = AsyncMock()
    hook.on_execute_tool_error = AsyncMock()

    async def speak() -> None:
        await asyncio.sleep(0.05)
        interrupt.set()

    task = asyncio.create_task(speak())
    results, events = await execute_tool_calls(
        registry,
        [ToolCallRequest(id="c1", name="web_search", arguments={})],
        concurrent=True,
        external_lookup_counts={},
        workspace_violation_counts={},
        hook=hook,
        context=MagicMock(),
        interrupt=interrupt,
    )
    await task

    assert results == [
        "Interrupted: the user sent a new message while this call was running. "
        "The call was abandoned. Do not retry it; handle the new message first."
    ]
    assert events[0]["status"] == "interrupted"
    hook.after_execute_tool.assert_not_awaited()
