"""Tests for the mid-turn injection system: drain, checkpoints, pending queues, error paths."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from yoreni.agent.context import TranscriptInput
from yoreni.config.schema import AgentDefaults
from yoreni.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _make_injection_callback(queue: asyncio.Queue):
    """Return an async callback that drains *queue* into a list of dicts."""
    async def inject_cb():
        items = []
        while not queue.empty():
            items.append(await queue.get())
        return items
    return inject_cb


@pytest.mark.asyncio
async def test_drain_injections_returns_empty_when_no_callback():
    """No injection_callback → empty list."""
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=None,
    )
    result = await runner._drain_injections(spec)
    assert result == []


@pytest.mark.asyncio
async def test_drain_injections_extracts_content_from_inbound_messages():
    """Should extract .content from InboundMessage objects."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    msgs = [
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hello"),
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="world"),
    ]

    async def cb():
        return msgs

    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "world"},
    ]


@pytest.mark.asyncio
async def test_drain_injections_keeps_entire_callback_snapshot():
    """A callback snapshot is never split by an arbitrary message count."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    msgs = [
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content=f"msg{i}")
        for i in range(8)
    ]

    async def cb():
        return msgs

    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [
        {"role": "user", "content": f"msg{i}"}
        for i in range(8)
    ]


@pytest.mark.asyncio
async def test_drain_injections_skips_empty_content():
    """Messages with blank content should be filtered out."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    msgs = [
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content=""),
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="   "),
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="valid"),
    ]

    async def cb():
        return msgs

    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [{"role": "user", "content": "valid"}]


@pytest.mark.asyncio
async def test_drain_injections_filters_empty_dict_payloads():
    """Pre-normalized dict injections should obey the same empty-content guard."""
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    multimodal = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]
    msgs = [
        {"role": "user", "content": ""},
        {"role": "user", "content": "   "},
        {"role": "user", "content": None},
        {"role": "assistant", "content": "should not be re-injected as user"},
        None,
        {"role": "user", "content": "valid"},
        {"role": "user", "content": multimodal},
    ]

    async def cb():
        return msgs

    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [
        {"role": "user", "content": "valid"},
        {"role": "user", "content": multimodal},
    ]


@pytest.mark.asyncio
async def test_drain_injections_skips_objects_with_none_content():
    """Objects exposing content=None should be skipped rather than stringified."""
    from types import SimpleNamespace

    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    async def cb():
        return [
            SimpleNamespace(content=None),
            SimpleNamespace(content=""),
            SimpleNamespace(content="valid"),
        ]

    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [{"role": "user", "content": "valid"}]


@pytest.mark.asyncio
async def test_drain_injections_handles_callback_exception():
    """If the callback raises, return empty list (error is logged)."""
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    runner = AgentRunner()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    async def cb():
        raise RuntimeError("boom")

    spec = make_run_spec(provider,
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == []


@pytest.mark.asyncio
async def test_checkpoint1_injects_after_tool_execution():
    """Follow-up messages are injected after tool execution, before next LLM call."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    captured_messages = []

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        captured_messages.append(list(messages))
        if call_count["n"] == 1:
            return LLMResponse(
                content="using tool",
                tool_calls=[ToolCallRequest(id="c1", name="read_file", arguments={"path": "x"})],
                usage=None,
            )
        return LLMResponse(content="final answer", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    async def execute_tool(*_args, **_kwargs):
        await injection_queue.put(
            InboundMessage(
                channel="cli", sender_id="u", chat_id="c", content="follow-up question"
            )
        )
        return "file content"

    tools.execute = AsyncMock(side_effect=execute_tool)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "final answer"
    # The second call should have the injected user message
    assert call_count["n"] == 2
    last_messages = captured_messages[-1]
    injected = [m for m in last_messages if m.get("role") == "user" and m.get("content") == "follow-up question"]
    assert len(injected) == 1


@pytest.mark.asyncio
async def test_terminal_wait_does_not_block_next_iteration_after_tools():
    """Background waits begin only after a no-tool response is ready to finish."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    second_request_started = asyncio.Event()
    allow_final_response = asyncio.Event()
    terminal_wait_started = asyncio.Event()
    release_terminal_result = asyncio.Event()
    call_count = 0
    terminal_result_delivered = False

    async def chat_stream_with_retry(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content="working",
                tool_calls=[ToolCallRequest(id="c1", name="read_file", arguments={"path": "x"})],
            )
        if call_count == 2:
            second_request_started.set()
            await allow_final_response.wait()
            return LLMResponse(content="main work finished", tool_calls=[])
        return LLMResponse(content="combined final answer", tool_calls=[])

    async def drain_available():
        return []

    async def wait_at_terminal():
        nonlocal terminal_result_delivered
        if terminal_result_delivered:
            return []
        terminal_wait_started.set()
        await release_terminal_result.wait()
        terminal_result_delivered = True
        return [
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="c",
                content="background result",
            )
        ]

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="file content")

    runner = AgentRunner()
    run_task = asyncio.create_task(runner.run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=drain_available,
        terminal_injection_callback=wait_at_terminal,
    )))

    await asyncio.wait_for(second_request_started.wait(), timeout=1.0)
    assert not terminal_wait_started.is_set()

    allow_final_response.set()
    await asyncio.wait_for(terminal_wait_started.wait(), timeout=1.0)
    assert not run_task.done()

    release_terminal_result.set()
    result = await asyncio.wait_for(run_task, timeout=1.0)

    assert call_count == 3
    assert result.had_injections is True
    assert result.final_content == "combined final answer"


@pytest.mark.asyncio
async def test_goal_continuation_precedes_terminal_wait():
    """An active sustained goal keeps running without joining background work."""
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    provider.chat_stream_with_retry = AsyncMock(side_effect=[
        LLMResponse(content="goal checkpoint", tool_calls=[]),
        LLMResponse(content="goal complete", tool_calls=[]),
    ])
    tools = MagicMock()
    tools.get_definitions.return_value = []
    continuation_checks = 0
    terminal_waits = 0

    async def continue_goal() -> str | None:
        nonlocal continuation_checks
        continuation_checks += 1
        return "Continue the active goal." if continuation_checks == 1 else None

    async def drain_available():
        return []

    async def wait_at_terminal():
        nonlocal terminal_waits
        terminal_waits += 1
        return []

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "complete the goal"}],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=drain_available,
        terminal_injection_callback=wait_at_terminal,
        continuation_callback=continue_goal,
    ))

    assert provider.chat_stream_with_retry.await_count == 2
    assert terminal_waits == 1
    assert result.final_content == "goal complete"


@pytest.mark.asyncio
async def test_checkpoint2_injects_after_final_response_with_resuming_stream():
    """After final response, if injections exist, stream_end should get resuming=True."""
    from yoreni.agent.hook import AgentHook, AgentHookContext
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    stream_end_calls = []

    class TrackingHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            stream_end_calls.append(resuming)

        def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
            return content

    async def chat_stream_with_retry(*, messages, on_content_delta=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            await injection_queue.put(
                InboundMessage(
                    channel="cli", sender_id="u", chat_id="c", content="quick follow-up"
                )
            )
            return LLMResponse(content="first answer", tool_calls=[], usage=None)
        return LLMResponse(content="second answer", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=TrackingHook(),
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "second answer"
    assert call_count["n"] == 2
    # First stream_end should have resuming=True (because injections found)
    assert stream_end_calls[0] is True
    # Second (final) stream_end should have resuming=False
    assert stream_end_calls[-1] is False


@pytest.mark.asyncio
async def test_injected_followup_starts_new_length_recovery_chain():
    """A follow-up gets a fresh recovery budget and no content from the prior answer."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    responses = [
        LLMResponse(content="first-1 ", finish_reason="length"),
        LLMResponse(content="first-2 ", finish_reason="length"),
        LLMResponse(content="first-3 ", finish_reason="length"),
        LLMResponse(content="first-final", finish_reason="stop"),
        LLMResponse(content="follow-up ", finish_reason="length"),
        LLMResponse(content="answer", finish_reason="stop"),
    ]
    tools = MagicMock()
    tools.get_definitions.return_value = []

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)
    call_count = 0

    async def chat_stream_with_retry(**_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 4:
            await injection_queue.put(InboundMessage(
                channel="cli", sender_id="u", chat_id="c", content="follow-up question"
            ))
        return responses[call_count - 1]

    provider.chat_stream_with_retry = chat_stream_with_retry

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "give a long answer"}],
        tools=tools,
        model="test-model",
        max_iterations=8,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "follow-up answer"
    assert call_count == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("max_iterations", [1, 3])
async def test_truncated_answer_followup_requires_remaining_iteration(max_iterations):
    from yoreni.agent.hook import AgentHook, AgentHookContext
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    queue = asyncio.Queue()
    requests = []
    endings = []

    class StreamingHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            endings.append((resuming, context.stream_continues_current_message))

    async def chat_stream_with_retry(*, messages, on_content_delta=None, **kwargs):
        requests.append([dict(message) for message in messages])
        if len(requests) == 1:
            queue.put_nowait({"role": "user", "content": "Never mind. What is 2+2?"})
            if on_content_delta is not None:
                await on_content_delta("Unfinished old answer: ")
            return LLMResponse(content="Unfinished old answer: ", finish_reason="length")
        return LLMResponse(content="4", finish_reason="stop")

    provider.chat_stream_with_retry = chat_stream_with_retry
    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "old question"}],
        tools=tools,
        model="test-model",
        max_iterations=max_iterations,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=StreamingHook(),
        injection_callback=_make_injection_callback(queue),
    ))

    assert len(requests) == 2
    second_request = "\n".join(str(message.get("content", "")) for message in requests[1])
    if max_iterations == 1:
        assert "Never mind. What is 2+2?" not in second_request
        assert queue.get_nowait()["content"] == "Never mind. What is 2+2?"
        assert result.had_injections is False
        assert result.final_content == "Unfinished old answer:\n\n4"
        assert "Continue the same response from its exact endpoint" in second_request
    else:
        assert result.final_content == "4"
        assert endings[0] == (True, False)
        assert "Never mind. What is 2+2?" in second_request
        assert queue.empty()
        assert "Continue the same response from its exact endpoint" not in second_request


@pytest.mark.asyncio
async def test_checkpoint2_preserves_final_response_in_history_before_followup():
    """A follow-up injected after a final answer must still see that answer in history."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    captured_messages = []

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        captured_messages.append([dict(message) for message in messages])
        if call_count["n"] == 1:
            await injection_queue.put(
                InboundMessage(
                    channel="cli", sender_id="u", chat_id="c", content="follow-up question"
                )
            )
            return LLMResponse(content="first answer", tool_calls=[], usage=None)
        return LLMResponse(content="second answer", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.final_content == "second answer"
    assert call_count["n"] == 2
    assert captured_messages[-1] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow-up question"},
    ]
    assert [
        {"role": message["role"], "content": message["content"]}
        for message in result.messages
        if message.get("role") == "assistant"
    ] == [
        {"role": "assistant", "content": "first answer"},
        {"role": "assistant", "content": "second answer"},
    ]






@pytest.mark.asyncio
async def test_model_request_merges_injected_user_messages_without_losing_media():
    """The model copy may merge follow-ups while the raw transcript keeps each event."""
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()
    call_count = {"n": 0}
    captured_messages = []

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        captured_messages.append([dict(message) for message in messages])
        if call_count["n"] == 1:
            return LLMResponse(content="first answer", tool_calls=[], usage=None)
        return LLMResponse(content="second answer", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    async def inject_cb():
        if call_count["n"] == 1:
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                        {"type": "text", "text": "look at this"},
                    ],
                },
                {"role": "user", "content": "and answer briefly"},
            ]
        return []

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.final_content == "second answer"
    assert call_count["n"] == 2
    second_call = captured_messages[-1]
    user_messages = [message for message in second_call if message.get("role") == "user"]
    assert len(user_messages) == 2
    injected = user_messages[-1]
    assert isinstance(injected["content"], list)
    assert any(
        block.get("type") == "image_url"
        for block in injected["content"]
        if isinstance(block, dict)
    )
    assert any(
        block.get("type") == "text" and block.get("text") == "and answer briefly"
        for block in injected["content"]
        if isinstance(block, dict)
    )
    assert [message["content"] for message in result.messages[-3:-1]] == [
        [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            {"type": "text", "text": "look at this"},
        ],
        "and answer briefly",
    ]


def test_model_request_merge_preserves_runtime_markers_with_media() -> None:
    from yoreni.agent.context_governance import ContextGovernor
    from yoreni.agent.runner import AgentRunner
    from yoreni.runtime_context import (
        RUNTIME_CONTEXT_HISTORY_META,
        RUNTIME_CONTEXT_MESSAGE_META,
        RuntimeContextBlock,
        append_runtime_context,
        public_history_message,
    )

    first_visible = [
        {"type": "text", "text": "first"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]
    first_content, first_marker = append_runtime_context(
        first_visible,
        [RuntimeContextBlock(source="first", content="private first")],
    )
    second_content, second_marker = append_runtime_context(
        "second",
        [RuntimeContextBlock(source="second", content="private second")],
    )
    messages: list[dict] = []

    AgentRunner._append_injected_messages(messages, [
        {
            "role": "user",
            "content": first_content,
            "_meta": {RUNTIME_CONTEXT_MESSAGE_META: first_marker},
        },
        {
            "role": "user",
            "content": second_content,
            "_meta": {RUNTIME_CONTEXT_MESSAGE_META: second_marker},
        },
    ])

    assert len(messages) == 2
    merged = ContextGovernor._merge_adjacent_user_messages_for_model(messages)[0]
    assert len(messages) == 2
    assert "private first" in str(merged["content"])
    assert "private second" in str(merged["content"])
    persisted = {
        "role": "user",
        "content": merged["content"],
        RUNTIME_CONTEXT_HISTORY_META: merged["_meta"][RUNTIME_CONTEXT_MESSAGE_META],
    }
    assert public_history_message(persisted)["content"] == [
        *first_visible,
        {"type": "text", "text": "second"},
    ]


@pytest.mark.asyncio
async def test_injection_cycles_are_not_stopped_by_an_arbitrary_cap():
    """Every pending snapshot runs until the normal iteration budget is reached."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    injection_queue = asyncio.Queue()

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 7:
            await injection_queue.put(InboundMessage(
                channel="cli",
                sender_id="u",
                chat_id="c",
                content=f"msg-{call_count['n']}",
            ))
        return LLMResponse(content=f"answer-{call_count['n']}", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    inject_cb = _make_injection_callback(injection_queue)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "start"}],
        tools=tools,
        model="test-model",
        max_iterations=20,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert call_count["n"] == 8


@pytest.mark.asyncio
async def test_no_injections_flag_is_false_by_default():
    """had_injections should be False when no injection callback or no messages."""
    from yoreni.agent.runner import AgentRunner

    provider = MagicMock()

    async def chat_stream_with_retry(**kwargs):
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.had_injections is False
















@pytest.mark.asyncio
async def test_drain_injections_after_recoverable_tool_error():
    """A tool error and injected follow-up continue in the same runner conversation."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="stale prefix ",
                finish_reason="length",
                usage=None,
            )
        if call_count["n"] == 2:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="exec", arguments={"cmd": "bad"})],
                usage=None,
            )
        # Third call: respond normally to the injected follow-up.
        return LLMResponse(content="reply to follow-up", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=RuntimeError("tool exploded"))

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    await injection_queue.put(
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="follow-up after error")
    )

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "reply to follow-up"
    assert call_count["n"] == 3
    # The injection should be in the messages history
    injected = [
        m for m in result.messages
        if m.get("role") == "user" and m.get("content") == "follow-up after error"
    ]
    assert len(injected) == 1


@pytest.mark.asyncio
async def test_drain_injections_on_llm_error():
    """A follow-up after an error stays raw and reaches the next model request."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    requests: list[list[dict]] = []

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        requests.append(messages)
        if call_count["n"] == 1:
            await injection_queue.put(
                InboundMessage(
                    channel="cli",
                    sender_id="u",
                    chat_id="c",
                    content="follow-up after LLM error",
                )
            )
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="error",
                usage=None,
            )
        # Second call: respond normally to the injected follow-up
        return LLMResponse(content="recovered answer", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=None,
        transcript_input=TranscriptInput(
            history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "previous response"},
                {"role": "user", "content": "trigger error"},
            ],
            current_message=None,
        ),
        transcript_builder=lambda transcript: [
            {"role": "system", "content": "system"},
            *transcript.history,
        ],
        consolidate_history=AsyncMock(return_value=None),
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "recovered answer"
    assert "follow-up after LLM error" in str(requests[1])
    assert [
        message["content"]
        for message in result.messages
        if message.get("role") == "user"
    ][-2:] == [
        "trigger error",
        "follow-up after LLM error",
    ]


@pytest.mark.asyncio
async def test_drain_injections_on_empty_final_response():
    """Pending injections should be drained when the runner exits due to empty response."""
    from yoreni.agent.runner import _MAX_EMPTY_RETRIES, AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= _MAX_EMPTY_RETRIES + 1:
            if call_count["n"] == _MAX_EMPTY_RETRIES + 1:
                await injection_queue.put(InboundMessage(
                    channel="cli",
                    sender_id="u",
                    chat_id="c",
                    content="follow-up after empty",
                ))
            return LLMResponse(content="", tool_calls=[], usage=None)
        # After retries exhausted + injection drain, respond normally
        return LLMResponse(content="answer after empty", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "previous response"},
            {"role": "user", "content": "trigger empty"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=10,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "answer after empty"
    injected = [
        m for m in result.messages
        if m.get("role") == "user" and "follow-up after empty" in str(m.get("content", ""))
    ]
    assert len(injected) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stop_reason", ["error", "empty_final_response"])
async def test_late_injection_recovery_completes_and_delivers_reply(failed_stop_reason):
    """Input arriving after failure bookkeeping must not poison a recovered turn."""
    from yoreni.agent.hook import AgentHook
    from yoreni.agent.runner import _MAX_EMPTY_RETRIES, AgentRunner
    from yoreni.agent.tools.registry import ToolRegistry
    from yoreni.agent.turn_delivery import TurnDeliveryFactory
    from yoreni.bus.events import InboundMessage, OutboundMessage
    from yoreni.bus.queue import MessageBus
    from yoreni.bus.runtime_events import TurnCompleted
    from yoreni.providers.base import LLMProvider

    failed_responses = (
        [LLMResponse(content="temporary failure", finish_reason="error")]
        if failed_stop_reason == "error"
        else [LLMResponse(content="") for _ in range(_MAX_EMPTY_RETRIES + 1)]
    )
    provider = MagicMock(spec=LLMProvider)
    provider.chat_stream_with_retry = AsyncMock(side_effect=[
        *failed_responses,
        LLMResponse(content="recovered answer"),
    ])
    injection_queue = asyncio.Queue()
    observed_failures = []

    class InjectAfterFailureHook(AgentHook):
        async def after_iteration(self, context) -> None:
            if context.stop_reason == failed_stop_reason and context.error is not None:
                observed_failures.append(context.error)
                # The earlier final-response drain has already run. Inject at
                # the late drain, after the runner has recorded the failure.
                await injection_queue.put(InboundMessage(
                    channel="websocket",
                    sender_id="user",
                    chat_id="chat",
                    content="Please retry now",
                ))

    hook = InjectAfterFailureHook()
    hook.on_error = AsyncMock()
    result = await AgentRunner().run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=ToolRegistry(),
        model="test-model",
        max_iterations=_MAX_EMPTY_RETRIES + 2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=_make_injection_callback(injection_queue),
        hook=hook,
    ))

    bus = MessageBus()
    completions: list[TurnCompleted] = []
    bus.subscribe(completions.append, TurnCompleted)
    msg = InboundMessage(
        channel="websocket", sender_id="user", chat_id="chat", content="hello",
    )
    delivery = TurnDeliveryFactory(bus).create(msg, "run-1")
    delivery.record_stop_reason(result.stop_reason, failure_error_kind=result.failure_error_kind)
    await delivery.complete(
        OutboundMessage(channel="websocket", chat_id="chat", content=result.final_content),
        publish_completion=True,
    )

    assert len(observed_failures) == 1
    assert provider.chat_stream_with_retry.await_count == len(failed_responses) + 1
    assert result.had_injections is True
    assert result.final_content == "recovered answer"
    assert bus.outbound_size == 1
    assert bus.outbound.get_nowait().content == "recovered answer"
    assert result.stop_reason == "completed"
    assert result.error is None
    assert result.failure_error_kind is None
    hook.on_error.assert_not_awaited()
    assert len(completions) == 1
    assert completions[0].outcome == "completed"
    assert completions[0].failure_kind is None
    assert completions[0].failure_error_kind is None


@pytest.mark.asyncio
async def test_max_iterations_without_finalization_keeps_late_injection_queued():
    """Never consume a user message when no later model request can observe it."""
    from yoreni.agent.hook import AgentHook
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        return LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id=f"c{call_count['n']}", name="read_file", arguments={"path": "x"})],
            usage=None,
        )

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="file content")

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    class InjectAfterLastIterationHook(AgentHook):
        async def after_iteration(self, context) -> None:
            if context.iteration == 1:
                await injection_queue.put(InboundMessage(
                    channel="cli",
                    sender_id="u",
                    chat_id="c",
                    content="follow-up after max iters",
                ))

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
        hook=InjectAfterLastIterationHook(),
        finalize_on_max_iterations=False,
    ))

    assert result.stop_reason == "max_iterations"
    assert result.had_injections is False
    assert injection_queue.qsize() == 1
    assert (await injection_queue.get()).content == "follow-up after max iters"


@pytest.mark.asyncio
async def test_max_iterations_finalization_keeps_late_injection_queued():
    """New input waits for a turn with tools instead of entering no-tools finalization."""
    from yoreni.agent.hook import AgentHook
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    captured_messages: list[list[dict]] = []

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        captured_messages.append([dict(message) for message in messages])
        return LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id=f"c{call_count['n']}", name="read_file", arguments={"path": "x"})],
            usage=None,
        )

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="file content")

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    class InjectOnLastAfterIterationHook(AgentHook):
        def __init__(self) -> None:
            self.after_iteration_calls = 0

        async def after_iteration(self, context) -> None:
            self.after_iteration_calls += 1
            if self.after_iteration_calls == 2:
                await injection_queue.put(
                    InboundMessage(
                        channel="cli",
                        sender_id="u",
                        chat_id="c",
                        content="late follow-up after max iters",
                    )
                )

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
        hook=InjectOnLastAfterIterationHook(),
    ))

    assert result.stop_reason == "max_iterations"
    assert result.had_injections is False
    assert injection_queue.get_nowait().content == "late follow-up after max iters"
    assert call_count["n"] == 3
    finalization_request = "\n".join(
        str(message.get("content", "")) for message in captured_messages[-1]
    )
    assert "late follow-up after max iters" not in finalization_request
    injected = [
        m for m in result.messages
        if m.get("role") == "user" and m.get("content") == "late follow-up after max iters"
    ]
    assert injected == []


@pytest.mark.asyncio
async def test_error_path_is_not_stopped_by_an_arbitrary_injection_cap():
    """Error recovery consumes every arrived snapshot until no follow-up remains."""
    from yoreni.agent.runner import AgentRunner
    from yoreni.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    injection_queue = asyncio.Queue()

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 7:
            await injection_queue.put(InboundMessage(
                channel="cli",
                sender_id="u",
                chat_id="c",
                content=f"msg-{call_count['n']}",
            ))
        return LLMResponse(
            content=None,
            tool_calls=[],
            finish_reason="error",
            usage=None,
        )

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    inject_cb = _make_injection_callback(injection_queue)

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "previous"},
            {"role": "user", "content": "trigger error"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=20,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert call_count["n"] == 8
