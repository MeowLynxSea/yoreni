"""Tests for transcript checkpoint compaction.

Compaction keeps an in-flight Run inside its context window. It is not a fact
pipeline: a failed summarisation must surface as an error, never as a raw dump
that silently replaces the checkpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yoreni.agent.context_compaction import Consolidator, ContextCompactionError
from yoreni.providers.base import (
    GenerationSettings,
    LLMResponse,
    ProviderConversationState,
    ToolCallRequest,
)
from yoreni.utils.llm_runtime import LLMRuntime
from yoreni.utils.prompt_templates import render_template

_ARCHIVE_PROMPT = render_template("agent/consolidator_archive.md", strip=True)


@pytest.fixture
def mock_provider():
    provider = MagicMock(aclose=AsyncMock())
    provider.chat_stream_with_retry = AsyncMock()
    provider.generation = GenerationSettings(max_tokens=100)
    provider.can_resume_conversation_state.return_value = True
    return provider


@pytest.fixture
def runtime(mock_provider):
    return LLMRuntime.capture(mock_provider, "test-model", context_window_tokens=128_000)


@pytest.fixture
def consolidator():
    return Consolidator()


def _provider_state() -> ProviderConversationState:
    return ProviderConversationState(
        kind="openai_responses",
        provider="openai:test",
        model="test-model",
        version=1,
        payload={"items": []},
    )


class TestSummarizeTranscript:
    async def test_summarizes_the_exact_accepted_prefix(
        self, consolidator, mock_provider, runtime,
    ):
        accepted = [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "accepted history"},
        ]
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(
            content="replacement checkpoint",
        )

        result = await consolidator.summarize_transcript(
            accepted,
            "previous checkpoint",
            runtime=runtime,
            run_id="test:turn",
            tools=[{"type": "function", "function": {"name": "inspect"}}],
        )

        assert result == "replacement checkpoint"
        call = mock_provider.chat_stream_with_retry.await_args.kwargs
        assert call["messages"][:-1] == accepted
        assert call["messages"][-1]["role"] == "user"
        assert "SNIP" in call["messages"][-1]["content"]
        assert call["model"] == runtime.model

    async def test_uses_the_captured_generation_settings(
        self, consolidator, mock_provider,
    ):
        provider = mock_provider
        provider.generation = GenerationSettings(
            temperature=0.25, max_tokens=321, reasoning_effort="medium",
        )
        admitted = LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)
        provider.generation = GenerationSettings(
            temperature=0.9, max_tokens=999, reasoning_effort="high",
        )
        provider.chat_stream_with_retry.return_value = LLMResponse(content="Summary.")

        await consolidator.summarize_transcript(
            [{"role": "user", "content": "hello"}],
            None,
            runtime=admitted,
            run_id="test:turn",
            tools=[],
        )

        call = provider.chat_stream_with_retry.call_args.kwargs
        assert call["model"] == admitted.model
        assert call["temperature"] == 0.25
        assert call["max_tokens"] == 321
        assert call["reasoning_effort"] == "medium"

    async def test_empty_transcript_returns_none(self, consolidator, runtime):
        assert await consolidator.summarize_transcript(
            [{"role": "system", "content": "only instructions"}],
            None,
            runtime=runtime,
            run_id="test:turn",
            tools=[],
        ) is None

    @pytest.mark.parametrize("finish_reason", ["error", "length"])
    async def test_incomplete_response_raises(
        self, consolidator, mock_provider, runtime, finish_reason,
    ):
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(
            content="partial output", finish_reason=finish_reason,
        )

        with pytest.raises(ContextCompactionError, match=finish_reason):
            await consolidator.summarize_transcript(
                [{"role": "user", "content": "hello"}],
                None,
                runtime=runtime,
                run_id="test:turn",
                tools=[],
            )

    async def test_provider_failure_raises_without_a_raw_dump(
        self, consolidator, mock_provider, runtime,
    ):
        mock_provider.chat_stream_with_retry.side_effect = RuntimeError("API error")

        with pytest.raises(ContextCompactionError, match="provider call failed"):
            await consolidator.summarize_transcript(
                [{"role": "user", "content": "hello"}],
                None,
                runtime=runtime,
                run_id="test:turn",
                tools=[],
            )

    async def test_empty_summary_raises(self, consolidator, mock_provider, runtime):
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(content="   ")

        with pytest.raises(ContextCompactionError, match="no summary"):
            await consolidator.summarize_transcript(
                [{"role": "user", "content": "hello"}],
                None,
                runtime=runtime,
                run_id="test:turn",
                tools=[],
            )

    async def test_oversized_input_is_rejected_before_the_call(
        self, consolidator, mock_provider,
    ):
        runtime = LLMRuntime.capture(mock_provider, "test-model", context_window_tokens=1000)
        mock_provider.estimate_prompt_tokens.return_value = (10_000, "test")

        with pytest.raises(ContextCompactionError, match="does not fit"):
            await consolidator.summarize_transcript(
                [{"role": "user", "content": "x" * 20_000}],
                None,
                runtime=runtime,
                run_id="test:turn",
                tools=[],
            )

        mock_provider.chat_stream_with_retry.assert_not_awaited()

    async def test_tool_calls_are_retried_once_without_tools(
        self, consolidator, mock_provider, runtime,
    ):
        mock_provider.chat_stream_with_retry.side_effect = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call-1", name="inspect", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="replacement checkpoint"),
        ]

        result = await consolidator.summarize_transcript(
            [{"role": "user", "content": "hello"}],
            None,
            runtime=runtime,
            run_id="test:turn",
            tools=[{"type": "function", "function": {"name": "inspect"}}],
        )

        assert result == "replacement checkpoint"
        _first, second = mock_provider.chat_stream_with_retry.await_args_list
        assert second.kwargs["messages"][-2]["role"] == "assistant"
        assert second.kwargs["messages"][-1]["role"] == "tool"
        assert "does not execute tools" in second.kwargs["messages"][-1]["content"]

    async def test_persistent_tool_calls_raise(
        self, consolidator, mock_provider, runtime,
    ):
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="call-1", name="inspect", arguments={})],
            finish_reason="tool_calls",
        )

        with pytest.raises(ContextCompactionError, match="tool calls"):
            await consolidator.summarize_transcript(
                [{"role": "user", "content": "hello"}],
                None,
                runtime=runtime,
                run_id="test:turn",
                tools=[{"type": "function", "function": {"name": "inspect"}}],
            )


class TestNativeCompaction:
    async def test_only_the_archive_prompt_is_appended(
        self, consolidator, mock_provider, runtime,
    ):
        accepted = [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "raw history must not be replayed"},
        ]
        state = _provider_state()
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(
            content="replacement checkpoint",
        )

        result = await consolidator.summarize_provider_compaction(
            state,
            accepted,
            "previous checkpoint",
            runtime=runtime,
            run_id="test:turn",
            tools=[{"type": "function", "function": {"name": "inspect"}}],
        )

        assert result == "replacement checkpoint"
        call = mock_provider.chat_stream_with_retry.await_args.kwargs
        assert call["messages"][0] == accepted[0]
        assert call["messages"][-1]["content"] == _ARCHIVE_PROMPT
        assert accepted[1] not in call["messages"]
        assert call["tools"] == []
        provider_context = call["provider_context"]
        assert provider_context.conversation_state is not None
        assert provider_context.conversation_state.payload == state.payload
        assert provider_context.conversation_state.pending_messages == [call["messages"][-1]]

    async def test_response_state_carries_the_tool_recovery_turn(
        self, consolidator, mock_provider, runtime,
    ):
        accepted = [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "raw history must not be replayed"},
        ]
        incoming_state = _provider_state()
        response_state = ProviderConversationState(
            kind="openai_responses",
            provider="openai:test",
            model="test-model",
            version=1,
            payload={"items": [{"type": "function_call", "call_id": "call-1"}]},
        )
        mock_provider.chat_stream_with_retry.side_effect = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call-1", name="inspect", arguments={})],
                finish_reason="tool_calls",
                provider_state=response_state,
            ),
            LLMResponse(content="replacement checkpoint"),
        ]

        result = await consolidator.summarize_provider_compaction(
            incoming_state,
            accepted,
            "previous checkpoint",
            runtime=runtime,
            run_id="test:turn",
            tools=[{"type": "function", "function": {"name": "inspect"}}],
        )

        assert result == "replacement checkpoint"
        first_call, recovery_call = mock_provider.chat_stream_with_retry.await_args_list
        assert first_call.kwargs["tools"] == recovery_call.kwargs["tools"] == []
        recovery_context = recovery_call.kwargs["provider_context"]
        assert recovery_context.conversation_state is not None
        assert recovery_context.conversation_state.payload == response_state.payload
        assert recovery_context.conversation_state.pending_messages == [{
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "inspect",
            "content": (
                "Run archival does not execute tools. Use only the supplied conversation and "
                "return the requested compact checkpoint now; do not call another tool."
            ),
        }]


def test_archive_prompt_requests_a_cumulative_replacement_checkpoint():
    for section in ("## Merge rules", "## What to retain", "## Output"):
        assert section in _ARCHIVE_PROMPT
    assert "replacement checkpoint" in _ARCHIVE_PROMPT
    assert "[Archived Context Summary]" in _ARCHIVE_PROMPT
    assert "SNIP" in _ARCHIVE_PROMPT
    assert "(nothing)" in _ARCHIVE_PROMPT
    assert "history.jsonl" not in _ARCHIVE_PROMPT
