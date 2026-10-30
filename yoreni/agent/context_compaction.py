"""Transcript checkpoint compaction for an in-flight Run.

Compaction exists so a running turn can continue inside its context window.
It is not long-term memory: the summary is model context, never a fact, and a
failed summarisation is reported instead of replaced by a raw transcript dump.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from yoreni.llm_usage.context import llm_usage_source
from yoreni.providers.base import (
    LLMResponse,
    ProviderConversationState,
)
from yoreni.providers.conversation_state import ProviderConversationStateController
from yoreni.utils.helpers import (
    build_assistant_message,
    estimate_prompt_tokens_chain,
    truncate_text_to_tokens,
)
from yoreni.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from yoreni.utils.llm_runtime import LLMRuntime

_ARCHIVE_TOOL_RESULT = (
    "Run archival does not execute tools. Use only the supplied conversation and "
    "return the requested compact checkpoint now; do not call another tool."
)


class ContextCompactionError(RuntimeError):
    """A checkpoint could not be produced; the Run must not pretend otherwise."""


class Consolidator:
    """Produce a bounded working-state checkpoint for the current Run."""

    _SAFETY_BUFFER = 1024

    async def summarize_transcript(
        self,
        accepted_messages: list[dict[str, Any]],
        previous_summary: str | None,
        *,
        runtime: "LLMRuntime",
        run_id: str | None,
        tools: list[dict[str, Any]],
        provider_state: ProviderConversationState | None = None,
    ) -> str | None:
        """Summarize the exact transcript prefix already accepted by the model."""
        source_messages = [
            dict(message)
            for message in accepted_messages
            if message.get("role") != "system"
        ]
        if not source_messages:
            return None

        max_output_tokens = max(0, runtime.generation.max_tokens)
        input_token_budget = runtime.context_window_tokens - max_output_tokens
        checkpoint_tokens = min(
            max_output_tokens,
            max(1, (input_token_budget - self._SAFETY_BUFFER) // 2),
        )

        prompt = render_template(
            "agent/consolidator_archive.md",
            strip=True,
            archive_count=len(source_messages),
        )
        prompt_message = {"role": "user", "content": prompt}
        provider_context = None
        state_controller: ProviderConversationStateController | None = None
        state_messages: list[dict[str, Any]] = []
        call_tools = tools
        if provider_state is not None:
            instruction_messages: list[dict[str, Any]] = []
            for message in accepted_messages:
                if message.get("role") not in {"system", "developer"}:
                    break
                instruction_messages.append(dict(message))
            request_messages = [*instruction_messages, prompt_message]
            state_controller = ProviderConversationStateController(
                provider=runtime.provider,
                model=runtime.model,
                messages=state_messages,
                state=provider_state,
                run_id=run_id,
            )
            state_messages.append(dict(prompt_message))
            provider_context = state_controller.prepare_request(
                state_messages,
                context_window_tokens=runtime.context_window_tokens,
            )
            if provider_context is None or provider_context.conversation_state is None:
                raise ContextCompactionError(
                    "provider state cannot carry the compaction request"
                )
            call_tools = []
        else:
            request_messages = [*[dict(message) for message in accepted_messages], prompt_message]
            if input_token_budget > 0:
                estimated, source = estimate_prompt_tokens_chain(
                    runtime.provider,
                    runtime.model,
                    request_messages,
                    call_tools,
                )
                if estimated > input_token_budget:
                    raise ContextCompactionError(
                        "compaction input does not fit the model context: "
                        f"{estimated}/{input_token_budget} via {source}"
                    )

        response: LLMResponse | None = None
        for attempt in range(2):
            try:
                with llm_usage_source("dream"):
                    response = await runtime.provider.chat_stream_with_retry(
                        model=runtime.model,
                        messages=request_messages,
                        tools=call_tools,
                        temperature=runtime.generation.temperature,
                        max_tokens=runtime.generation.max_tokens,
                        reasoning_effort=runtime.generation.reasoning_effort,
                        provider_context=provider_context,
                    )
            except Exception as exc:
                raise ContextCompactionError(
                    f"compaction provider call failed: {type(exc).__name__}"
                ) from exc
            if response.should_execute_tools is not True or attempt == 1:
                break

            logger.info(
                "Compaction provider returned {} tool call(s); requesting a checkpoint",
                len(response.tool_calls),
            )
            assistant_message = build_assistant_message(
                response.content,
                tool_calls=[call.to_openai_tool_call() for call in response.tool_calls],
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
            tool_messages = [
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": _ARCHIVE_TOOL_RESULT,
                }
                for call in response.tool_calls
            ]
            request_messages = [*request_messages, assistant_message, *tool_messages]
            if state_controller is not None:
                state_controller.observe_response(response, state_messages)
                state_messages.extend([
                    state_controller.project_response_message(
                        dict(assistant_message), response,
                    ),
                    *[dict(message) for message in tool_messages],
                ])
                provider_context = state_controller.prepare_request(
                    state_messages,
                    context_window_tokens=runtime.context_window_tokens,
                )
                if provider_context is None or provider_context.conversation_state is None:
                    raise ContextCompactionError(
                        "provider state cannot carry the compaction retry"
                    )

        assert response is not None
        if response.finish_reason in {"error", "length"}:
            raise ContextCompactionError(
                f"compaction provider did not complete ({response.finish_reason})"
            )
        if response.has_tool_calls is True:
            raise ContextCompactionError("compaction provider returned tool calls")
        summary = (response.content or "").strip()
        if not summary:
            raise ContextCompactionError("compaction provider returned no summary")
        return truncate_text_to_tokens(summary, max(1, max_output_tokens or checkpoint_tokens))

    async def summarize_provider_compaction(
        self,
        state: ProviderConversationState,
        fallback_messages: list[dict[str, Any]],
        previous_summary: str | None,
        *,
        runtime: "LLMRuntime",
        run_id: str | None,
        tools: list[dict[str, Any]],
    ) -> str | None:
        """Prompt a native compacted state without replaying its raw history."""
        return await self.summarize_transcript(
            fallback_messages,
            previous_summary,
            runtime=runtime,
            run_id=run_id,
            tools=tools,
            provider_state=state,
        )


__all__ = ["Consolidator", "ContextCompactionError"]
