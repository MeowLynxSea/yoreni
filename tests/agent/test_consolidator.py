"""Tests for Memory checkpoint consolidation and history journaling."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yoreni.agent.memory import (
    _ARCHIVE_TOOL_RESULT,
    _HISTORY_ENTRY_HARD_CAP,
    Consolidator,
    MemoryStore,
)
from yoreni.providers.base import (
    GenerationSettings,
    LLMResponse,
    ProviderConversationState,
    ToolCallRequest,
)
from yoreni.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RuntimeContextBlock,
    append_runtime_context,
)
from yoreni.utils.llm_runtime import LLMRuntime
from yoreni.utils.prompt_templates import render_template

_ARCHIVE_PROMPT = render_template("agent/consolidator_archive.md", strip=True)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


@pytest.fixture
def mock_provider():
    p = MagicMock(aclose=AsyncMock())
    p.chat_stream_with_retry = AsyncMock()
    p.generation = GenerationSettings(max_tokens=100)
    return p


@pytest.fixture
def runtime(mock_provider):
    return LLMRuntime.capture(
        mock_provider,
        "test-model",
        context_window_tokens=1000,
    )


@pytest.fixture
def consolidator(store):
    return Consolidator(store=store)


def _provider_state() -> ProviderConversationState:
    return ProviderConversationState(
        kind="openai_responses",
        provider="openai:test",
        model="test-model",
        version=1,
        payload={"items": []},
    )




async def _archive(
    consolidator,
    messages,
    runtime,
    *,
    run_id="test:session",
    previous_summary=None,
):
    return await consolidator.archiver.archive(
        messages,
        runtime=runtime,
        run_id=run_id,
        history=[
            {"role": "system", "content": "system prompt"},
            *messages,
        ],
        request_tools=[],
        previous_summary=previous_summary,
    )


class TestTurnTranscriptSummary:
    @pytest.mark.parametrize("summary", ["replacement checkpoint", "(nothing)"])
    async def test_uses_exact_accepted_prefix_and_existing_archiver(
        self,
        consolidator,
        mock_provider,
        runtime,
        summary,
    ):
        runtime = replace(runtime, context_window_tokens=4096)
        accepted = [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "accepted history"},
        ]
        tools = [{"type": "function", "function": {"name": "inspect"}}]
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(
            content=summary,
        )

        result = await consolidator.summarize_transcript(
            accepted,
            "previous checkpoint",
            runtime=runtime,
            run_id="test:turn",
            tools=tools,
        )

        assert result == summary
        call = mock_provider.chat_stream_with_retry.await_args.kwargs
        assert call["messages"][:-1] == accepted
        assert call["messages"][-1]["role"] == "user"
        assert "SNIP" in call["messages"][-1]["content"]
        assert call["tools"] == tools

    async def test_nonpersistent_failure_returns_raw_checkpoint_without_journaling(
        self,
        consolidator,
        mock_provider,
        runtime,
        store,
    ):
        accepted = [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "accepted history"},
        ]
        mock_provider.chat_stream_with_retry.return_value = LLMResponse(content="")
        consolidator._SAFETY_BUFFER = 0

        result = await consolidator.summarize_transcript(
            accepted,
            None,
            runtime=runtime,
            run_id="test:ephemeral-turn",
            tools=[],
            persist=False,
        )

        assert result is not None
        assert "[RAW]" in result
        assert "accepted history" in result
        assert store.read_unprocessed_history(since_cursor=0) == []

    async def test_native_compaction_appends_only_archive_prompt(
        self,
        consolidator,
        mock_provider,
        runtime,
    ):
        accepted = [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "raw history must not be replayed"},
        ]
        state = _provider_state()
        mock_provider.can_resume_conversation_state.return_value = True
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
        assert provider_context.conversation_state.pending_messages == [
            call["messages"][-1],
        ]

    async def test_native_compaction_recovers_tool_call_from_response_state(
        self,
        consolidator,
        mock_provider,
        runtime,
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
        mock_provider.can_resume_conversation_state.return_value = True
        mock_provider.chat_stream_with_retry.side_effect = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call-1", name="inspect", arguments={})],
                finish_reason="tool_calls",
                provider_state=response_state,
            ),
            LLMResponse(content="replacement checkpoint", finish_reason="stop"),
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
            "content": _ARCHIVE_TOOL_RESULT,
        }]


class TestConsolidatorSummarize:
    def test_format_messages_keeps_media_only_user_turn(self):
        path = "/home/user/.yoreni/media/websocket/clip.mp4"

        formatted = MemoryStore._format_messages([
            {
                "role": "user",
                "content": "",
                "media": [path],
                "timestamp": "2026-07-27",
            }
        ])

        assert formatted == f"[2026-07-27] USER: [image: {path}]"

    async def test_archive_uses_captured_generation(
        self, consolidator, mock_provider, runtime
    ):
        admitted = replace(
            runtime,
            generation=GenerationSettings(
                temperature=0.25,
                max_tokens=321,
                reasoning_effort="medium",
            ),
        )
        mock_provider.generation = GenerationSettings(
            temperature=0.9,
            max_tokens=999,
            reasoning_effort="high",
        )
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="Summary.",
            finish_reason="stop",
        )

        await _archive(consolidator, [{"role": "user", "content": "hello"}], admitted)

        call = mock_provider.chat_stream_with_retry.call_args.kwargs
        assert call["model"] == admitted.model
        assert call["temperature"] == 0.25
        assert call["max_tokens"] == 321
        assert call["reasoning_effort"] == "medium"

    async def test_summarize_appends_to_history(
        self, consolidator, mock_provider, store, runtime
    ):
        """Consolidator should persist the LLM summary in history.jsonl."""
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="User fixed a bug in the auth module."
        )
        messages = [
            {"role": "user", "content": "fix the auth bug"},
            {"role": "assistant", "content": "Done, fixed the race condition."},
        ]
        result = await _archive(consolidator, messages, runtime)
        assert result == "User fixed a bug in the auth module."
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1

    async def test_summarize_appends_run_id_to_history(
        self,
        consolidator,
        mock_provider,
        store,
        runtime,
    ):
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="User fixed a bug in the auth module.",
            finish_reason="stop",
        )
        messages = [{"role": "user", "content": "fix the auth bug"}]

        await _archive(
            consolidator,
            messages,
            runtime,
            run_id="telegram:chat-1",
        )

        entries = store.read_unprocessed_history(since_cursor=0)
        assert entries[0]["run_id"] == "telegram:chat-1"

    async def test_summarize_raw_dumps_on_llm_failure(
        self, consolidator, mock_provider, store, runtime
    ):
        """On LLM failure, append raw messages to memory/history.jsonl."""
        mock_provider.chat_stream_with_retry.side_effect = Exception("API error")
        messages = [{"role": "user", "content": "hello"}]
        result = await _archive(consolidator, messages, runtime)
        assert result is not None
        assert "[RAW]" in result
        assert "hello" in result
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert "[RAW]" in entries[0]["content"]

    async def test_raw_dump_fallback_appends_run_id(
        self,
        consolidator,
        mock_provider,
        store,
        runtime,
    ):
        mock_provider.chat_stream_with_retry.side_effect = Exception("API error")
        messages = [{"role": "user", "content": "hello"}]

        await _archive(
            consolidator,
            messages,
            runtime,
            run_id="slack:chat-2",
        )

        entries = store.read_unprocessed_history(since_cursor=0)
        assert entries[0]["run_id"] == "slack:chat-2"

    async def test_raw_fallback_represents_previous_checkpoint_and_new_chunk(
        self,
        consolidator,
        mock_provider,
        runtime,
    ):
        runtime = replace(runtime, generation=GenerationSettings(max_tokens=256))
        mock_provider.chat_stream_with_retry.side_effect = RuntimeError("API error")

        result = await _archive(
            consolidator,
            [{"role": "user", "content": "NEW_MARKER " + "new " * 200}],
            runtime,
            previous_summary="OLD_MARKER " + "old " * 200,
        )

        assert result is not None
        assert "[Previous archived context]" in result
        assert "OLD_MARKER" in result
        assert "[Newly archived raw context]" in result
        assert "NEW_MARKER" in result
        assert "... (truncated)" in result

    async def test_summarize_skips_empty_messages(self, consolidator, runtime):
        result = await _archive(consolidator, [], runtime)
        assert result is None


class TestConsolidatorPromptContract:
    def test_archive_prompt_requests_a_cumulative_replacement_checkpoint(self):
        prompt = _ARCHIVE_PROMPT

        for section in ("## Merge rules", "## What to retain", "## Output"):
            assert section in prompt
        assert "replacement checkpoint" in prompt
        assert "[Archived Context Summary]" in prompt
        assert "current conversation state" in prompt
        assert "SNIP" in prompt
        for mark in ("[permanent]", "[durable]", "[ephemeral]", "[correction]"):
            assert mark in prompt
        assert "working-state handoff" in prompt
        assert "- [mark] fact" in prompt
        assert "[skip]" not in prompt
        assert "(nothing)" in prompt
        assert "history.jsonl" not in prompt


class TestConsolidatorArchiveErrorHandling:
    """archive() must fall back when the LLM does not complete its overview.

    Error responses include overloaded / quota failures from #3244; length
    responses contain a partial overview that is likewise unsafe to persist.
    """

    @pytest.mark.parametrize("finish_reason", ["error", "length"])
    async def test_archive_falls_back_on_incomplete_finish_reason(
        self,
        consolidator,
        mock_provider,
        store,
        runtime,
        finish_reason: str,
    ):
        """Incomplete LLM output should trigger raw_archive, not persist partial text."""
        invalid_output = f"INVALID_{finish_reason.upper()}_OUTPUT"
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content=invalid_output,
            finish_reason=finish_reason,
        )
        messages = [
            {"role": "user", "content": "fix the auth bug"},
            {"role": "assistant", "content": "Done, fixed the race condition."},
        ]
        result = await _archive(consolidator, messages, runtime)
        assert result is not None
        assert "[RAW]" in result
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert "[RAW]" in entries[0]["content"]
        assert invalid_output not in entries[0]["content"]

    async def test_archive_preserves_summary_on_success(
        self, consolidator, mock_provider, store, runtime
    ):
        """Normal LLM response should still produce a proper summary entry."""
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="User fixed a bug in the auth module.",
            finish_reason="stop",
        )
        messages = [
            {"role": "user", "content": "fix the auth bug"},
            {"role": "assistant", "content": "Done."},
        ]
        result = await _archive(consolidator, messages, runtime)
        assert result == "User fixed a bug in the auth module."
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert "[RAW]" not in entries[0]["content"]

    async def test_archive_propagates_history_write_failure(
        self, consolidator, mock_provider, runtime
    ):
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="Summary.",
            finish_reason="stop",
        )
        consolidator.store.append_history = MagicMock(side_effect=OSError("disk full"))
        consolidator.store.raw_archive = MagicMock()

        with pytest.raises(OSError, match="disk full"):
            await _archive(
                consolidator,
                [{"role": "user", "content": "important"}],
                runtime,
            )

        consolidator.store.raw_archive.assert_not_called()

    async def test_archive_propagates_template_failure_without_raw_archive(
        self, consolidator, mock_provider, runtime, monkeypatch
    ):
        runtime = replace(runtime, context_window_tokens=128_000)
        consolidator.store.raw_archive = MagicMock()
        monkeypatch.setattr(
            "yoreni.agent.memory.render_template",
            MagicMock(side_effect=RuntimeError("template failed")),
        )

        with pytest.raises(RuntimeError, match="template failed"):
            await _archive(consolidator, [{"role": "user", "content": "important"}], runtime)

        mock_provider.chat_stream_with_retry.assert_not_awaited()
        consolidator.store.raw_archive.assert_not_called()





class TestRawArchiveChunks:
    """raw_archive() keeps complete journal content with bounded individual entries."""

    @pytest.mark.parametrize("boundary", ["A ", "\n\n", "<think>PRIVATE</think>"])
    def test_raw_chunks_preserve_sanitized_boundaries(self, store, boundary):
        from yoreni.utils.helpers import strip_think

        message = {"role": "assistant", "content": ""}
        prefix_len = len(store._format_messages([{**message, "content": "x"}])) - 1
        message["content"] = "x" * (16_000 - prefix_len - 2) + boundary + "PUBLIC_TAIL"
        expected = strip_think(store._format_messages([message]))

        store.raw_archive([message])

        # Reload the real journal, rather than observing append call arguments.
        entries = MemoryStore(store.workspace).read_unprocessed_history(since_cursor=0)
        joined = "".join(entry["content"].split("\n", 1)[1] for entry in entries)
        assert joined == expected
        assert "PRIVATE" not in joined
        assert "PUBLIC_TAIL" in joined

    def test_raw_archive_splits_large_content_into_bounded_entries(self, store):
        """Large messages are split across bounded journal entries."""
        big = "x" * 50_000
        messages = [{"role": "user", "content": big}]
        store.raw_archive(messages)
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) > 1
        assert len(entries[0]["content"]) < 50_000
        assert "[RAW]" in entries[0]["content"]

    def test_raw_archive_preserves_small_content(self, store):
        """Small messages should not be truncated."""
        messages = [{"role": "user", "content": "hello"}]
        store.raw_archive(messages)
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert "hello" in entries[0]["content"]

    def test_raw_archive_returns_the_sanitized_persisted_checkpoint(self, store):
        messages = [
            {
                "role": "user",
                "content": "<think>PRIVATE_REASONING</think>visible result",
            }
        ]

        checkpoint = store.raw_archive(messages, run_id="cli:test")

        persisted = store.read_unprocessed_history(since_cursor=0)[0]["content"]
        assert checkpoint == persisted
        assert "PRIVATE_REASONING" not in checkpoint
        assert "visible result" in checkpoint

    def test_raw_archive_excludes_model_only_runtime_context(self, store):
        content, marker = append_runtime_context(
            "ship the feature",
            [RuntimeContextBlock(source="goal", content="host-only goal guidance")],
        )

        store.raw_archive([{
            "role": "user",
            "content": content,
            RUNTIME_CONTEXT_HISTORY_META: marker,
        }])

        entry = store.read_unprocessed_history(since_cursor=0)[0]["content"]
        assert "ship the feature" in entry
        assert "host-only goal guidance" not in entry

    def test_raw_archive_preserves_run_id(self, store):
        messages = [{"role": "user", "content": "hello"}]
        store.raw_archive(messages, run_id="websocket:chat-1")
        entries = store.read_unprocessed_history(since_cursor=0)
        assert entries[0]["run_id"] == "websocket:chat-1"

    def test_raw_archive_custom_max_chars(self, store):
        """max_chars parameter should override default limit."""
        messages = [{"role": "user", "content": "a" * 200}]
        store.raw_archive(messages, max_chars=100)
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries[0]["content"]) < 200

    @pytest.mark.parametrize("limit", [100, 100_000])
    def test_raw_chunks_keep_tail_with_custom_limit(self, store, limit):
        messages = [{"role": "user", "content": "x " * 40_000 + "TAIL_MARKER"}]
        expected = store._format_messages(messages)

        checkpoint = store.raw_archive(messages, max_chars=limit, run_id="cli:chunks")

        entries = MemoryStore(store.workspace).read_unprocessed_history(since_cursor=0)
        assert "".join(entry["content"].split("\n", 1)[1] for entry in entries) == expected
        assert all(len(entry["content"]) <= _HISTORY_ENTRY_HARD_CAP for entry in entries)
        assert all(entry["run_id"] == "cli:chunks" for entry in entries)
        assert checkpoint == store._build_raw_checkpoint(messages, max_chars=limit)


class TestArchivePersistence:
    async def test_archive_returns_the_sanitized_persisted_summary(
        self, consolidator, mock_provider, store, runtime
    ):
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="<think>PRIVATE_REASONING</think>safe summary",
            finish_reason="stop",
            has_tool_calls=False,
        )

        summary = await _archive(
            consolidator,
            [{"role": "user", "content": "hi"}],
            runtime,
        )

        persisted = store.read_unprocessed_history(since_cursor=0)[0]["content"]
        assert summary is not None
        assert summary == persisted == "safe summary"

    async def test_oversized_summary_uses_history_emergency_cap(
        self, consolidator, mock_provider, store, runtime
    ):
        """A pathologically large LLM summary must not land full-length in
        history.jsonl — that would re-open the #3412 bloat vector from the
        *success* path instead of the fallback path."""
        mock_provider.chat_stream_with_retry.return_value = MagicMock(
            content="S" * (_HISTORY_ENTRY_HARD_CAP * 2),
            finish_reason="stop",
        )
        summary = await _archive(
            consolidator,
            [{"role": "user", "content": "hi"}],
            runtime,
        )

        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert len(entry["content"]) <= _HISTORY_ENTRY_HARD_CAP + 50
        assert summary is not None
        assert summary == entry["content"]
