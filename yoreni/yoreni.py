"""High-level programmatic interface to yoreni."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from yoreni.agent.hook import AgentHook, SDKCaptureHook
from yoreni.agent.hooks import create_file_edit_activity_hook
from yoreni.agent.loop import AgentLoop
from yoreni.agent.state import AgentInput
from yoreni.agent.tools.image_generation import register_image_generation_tool
from yoreni.agent.tools.mcp import MCPProvider
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.config.schema import Config
from yoreni.providers.base import LLMUsage
from yoreni.sdk.clients import MemoryClient, RuntimeClient
from yoreni.sdk.runtime import ensure_single_model_selector
from yoreni.sdk.streaming import RunStream, SDKStreamEmitter, SDKStreamingHook
from yoreni.sdk.types import (
    STREAM_EVENT_REASONING_COMPLETED,
    STREAM_EVENT_REASONING_DELTA,
    STREAM_EVENT_RUN_COMPLETED,
    STREAM_EVENT_RUN_FAILED,
    STREAM_EVENT_RUN_STARTED,
    STREAM_EVENT_TEXT_COMPLETED,
    STREAM_EVENT_TEXT_DELTA,
    STREAM_EVENT_TOOL_COMPLETED,
    STREAM_EVENT_TOOL_FAILED,
    STREAM_EVENT_TOOL_STARTED,
    STREAM_EVENT_TYPES,
    RunResult,
    StreamEvent,
    StreamEventType,
    result_from_response,
)
from yoreni.utils.llm_runtime import LLMRuntime

__all__ = [
    "Yoreni",
    "AgentInput",
    "LLMUsage",
    "RunResult",
    "RunStream",
    "STREAM_EVENT_REASONING_COMPLETED",
    "STREAM_EVENT_REASONING_DELTA",
    "STREAM_EVENT_RUN_COMPLETED",
    "STREAM_EVENT_RUN_FAILED",
    "STREAM_EVENT_RUN_STARTED",
    "STREAM_EVENT_TEXT_COMPLETED",
    "STREAM_EVENT_TEXT_DELTA",
    "STREAM_EVENT_TOOL_COMPLETED",
    "STREAM_EVENT_TOOL_FAILED",
    "STREAM_EVENT_TOOL_STARTED",
    "STREAM_EVENT_TYPES",
    "StreamEvent",
    "StreamEventType",
]


class Yoreni:
    """Programmatic facade for running the yoreni agent.

    Usage::

        bot = Yoreni.from_config()
        result = await bot.run("Summarize this repo", hooks=[MyHook()])
        print(result.content)
    """

    def __init__(
        self,
        loop: AgentLoop,
        *,
        config: Config | None = None,
        mcp_provider: MCPProvider | None = None,
    ) -> None:
        self._loop = loop
        self._config = config
        self._mcp_provider = mcp_provider
        self.memory = MemoryClient(loop)
        self.runtime = RuntimeClient(loop)

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        workspace: str | Path | None = None,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> Yoreni:
        """Create a Yoreni instance from a config file.

        Args:
            config_path: Path to ``config.json``.  Defaults to
                ``~/.yoreni/config.json``.
            workspace: Override the workspace directory from config.
            model: Override the instance default model.
            model_preset: Override the instance default model preset.
        """
        from yoreni.config.loader import load_config, resolve_config_env_vars

        ensure_single_model_selector(model=model, model_preset=model_preset)
        resolved: Path | None = None
        if config_path is not None:
            resolved = Path(config_path).expanduser().resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Config not found: {resolved}")

        config: Config = resolve_config_env_vars(
            load_config(resolved),
            config_path=resolved,
        )
        if workspace is not None:
            config.agents.defaults.workspace = str(
                Path(workspace).expanduser().resolve()
            )
        if model is not None:
            config.agents.defaults.model_preset = None
            config.agents.defaults.model = model
            config.agents.defaults.provider = "auto"
        elif model_preset is not None:
            config.agents.defaults.model_preset = model_preset

        from yoreni.config.paths import get_media_dir
        from yoreni.sandbox import SandboxGateway, set_sandbox_gateway

        set_sandbox_gateway(SandboxGateway(
            config.tools.sandbox,
            host_workspace=str(config.workspace_path),
            host_media_dir=str(get_media_dir()),
        ))

        tools = ToolRegistry()
        register_image_generation_tool(config, tools, workspace=config.workspace_path)
        mcp_provider = MCPProvider.from_config(config, tools)
        from yoreni.profile.store import UserProfileStore

        loop = AgentLoop.from_config(
            config,
            hook_factories=[create_file_edit_activity_hook],
            tool_registry=tools,
            profile_store=(
                UserProfileStore(resolved.parent / "profile.json")
                if resolved is not None
                else None
            ),
        )
        return cls(loop, config=config, mcp_provider=mcp_provider)

    async def act(
        self, event: AgentInput, *, resume: bool = False,
        hooks: list[AgentHook] | None = None,
    ) -> RunResult:
        """Run a trusted event with the persistent identity and no chat session."""
        capture = SDKCaptureHook()
        if self._mcp_provider is not None:
            await self._mcp_provider.connect()
        response = await self._loop.process_event(
            event, resume=resume, hooks=[capture, *(hooks or [])],
        )
        return result_from_response(response, capture)

    async def run(
        self,
        message: str,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        ephemeral: bool = False,
        attributes: Mapping[str, Any] | None = None,
        hooks: list[AgentHook] | None = None,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> RunResult:
        """Run the agent once and return the result.

        Args:
            message: The user message to process.
            channel: Logical channel label for runtime context.
            chat_id: Logical chat identifier for runtime context.
            sender_id: Logical sender identifier for runtime context.
            media: Optional local media paths attached to the message.
            ephemeral: If true, do not retain the Run or its transcript.
            attributes: Optional caller-owned request data exposed to context
                providers and turn-hook factories. Attributes are kept separate
                from yoreni's trusted internal message metadata.
            hooks: Optional lifecycle hooks for this run.
            model: Override the model for this run only.
            model_preset: Override the model preset for this run only.
        """
        capture = SDKCaptureHook()
        per_run_hooks = [capture, *(hooks or [])]
        runtime = self._loop.runtime_resolver.resolve_override(
            model=model,
            model_preset=model_preset,
            config=self._config,
        )
        if self._mcp_provider is not None:
            await self._mcp_provider.connect()
        response = await self._loop.process_event(
            AgentInput(
                "sdk", message, principal="owner", channel=channel, chat_id=chat_id,
                media=tuple(media or ()), ephemeral=ephemeral,
                metadata={"sender_id": sender_id},
            ), runtime=runtime, hooks=per_run_hooks, attributes=dict(attributes or {}),
        )

        return result_from_response(response, capture)

    async def run_streamed(
        self,
        message: str,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        ephemeral: bool = False,
        attributes: Mapping[str, Any] | None = None,
        hooks: list[AgentHook] | None = None,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> RunStream:
        """Start a streamed run and return a handle for events and final result."""
        override_runtime = self._loop.runtime_resolver.resolve_override(
            model=model,
            model_preset=model_preset,
            config=self._config,
        )
        queue: asyncio.Queue[StreamEvent | object] = asyncio.Queue(maxsize=256)
        emitter = SDKStreamEmitter(queue)
        stream_hook = SDKStreamingHook(emitter)
        capture = SDKCaptureHook()
        per_run_hooks = [capture, stream_hook, *(hooks or [])]
        run_started = False

        async def _emit_run_started(runtime: LLMRuntime | None = None) -> None:
            nonlocal run_started
            if run_started:
                return
            if runtime is None:
                runtime = override_runtime
            metadata: dict[str, Any] = {
                "channel": channel,
                "chat_id": chat_id,
                "sender_id": sender_id,
            }
            if runtime is not None:
                metadata.update({
                    "model": runtime.model,
                    "model_preset": runtime.model_preset,
                })
            await emitter.emit(StreamEvent(
                type=STREAM_EVENT_RUN_STARTED,
                metadata=metadata,
            ))
            run_started = True

        async def _on_stream(delta: str) -> None:
            await emitter.text_delta(delta)

        async def _on_stream_end(*_args: Any, resuming: bool = False, **_kwargs: Any) -> None:
            await emitter.text_completed(resuming=resuming)

        async def _run() -> RunResult:
            try:
                if self._mcp_provider is not None:
                    await self._mcp_provider.connect()
                response = await self._loop.process_event(
                    AgentInput(
                        "sdk", message, principal="owner", channel=channel, chat_id=chat_id,
                        media=tuple(media or ()), ephemeral=ephemeral,
                        metadata={"sender_id": sender_id},
                    ), runtime=override_runtime, hooks=per_run_hooks,
                    on_stream=_on_stream, on_stream_end=_on_stream_end,
                    on_runtime_admitted=_emit_run_started, attributes=dict(attributes or {}),
                )
                await _emit_run_started()
                await emitter.text_completed(resuming=False, force=False)
                result = result_from_response(response, capture)
                await emitter.emit(StreamEvent(
                    type=STREAM_EVENT_RUN_COMPLETED,
                    content=result.content,
                    result=result,
                    usage=result.usage,
                    metadata=dict(result.metadata),
                ))
                return result
            except Exception as exc:
                await _emit_run_started()
                await emitter.emit(StreamEvent(
                    type=STREAM_EVENT_RUN_FAILED,
                    error=str(exc),
                    metadata={"exception_type": type(exc).__name__},
                ))
                raise
            finally:
                await emitter.close()

        task = asyncio.create_task(_run())
        return RunStream(task, queue)

    async def stream(
        self,
        message: str,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        ephemeral: bool = False,
        attributes: Mapping[str, Any] | None = None,
        hooks: list[AgentHook] | None = None,
        model: str | None = None,
        model_preset: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream events for one agent turn."""
        run = await self.run_streamed(
            message,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            media=media,
            ephemeral=ephemeral,
            attributes=attributes,
            hooks=hooks,
            model=model,
            model_preset=model_preset,
        )
        try:
            async for event in run.stream_events():
                yield event
            await run.wait()
        finally:
            if not run.done:
                await run.aclose()

    async def aclose(self) -> None:
        """Release resources held by this instance."""
        try:
            await self._loop.aclose()
        finally:
            if self._mcp_provider is not None:
                await self._mcp_provider.aclose()

    async def __aenter__(self) -> Yoreni:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
