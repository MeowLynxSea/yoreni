"""Agent execution composition, event dispatch and resource lifetime."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Coroutine, Mapping
from contextlib import AbstractContextManager, ExitStack, suppress
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, cast
from uuid import uuid4

from loguru import logger

from yoreni.agent import model_presets as preset_helpers
from yoreni.agent.context import ContextBuilder, TranscriptInput
from yoreni.agent.context_compaction import Consolidator
from yoreni.agent.goal_permission import GoalInputScope
from yoreni.agent.hook import AgentHook, AgentTurnHookFactory
from yoreni.agent.injection import TurnInjections
from yoreni.agent.model_runtime import ModelRuntimeResolver
from yoreni.agent.runner import (
    AgentRunner,
    AgentRunResult,
    AgentRunSpec,
    CheckpointCallback,
    ContinuationCallback,
    RequiredToolRound,
)
from yoreni.agent.state import AgentInput
from yoreni.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from yoreni.agent.tools.exec_session import ExecSessionManager
from yoreni.agent.tools.file_state import FileStates, bind_file_states, reset_file_states
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.agent.tools.runtime_control import AgentRuntimeControl
from yoreni.agent.turn_delivery import TurnDeliveryFactory
from yoreni.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from yoreni.bus.events import InboundMessage, OutboundMessage
from yoreni.bus.outbound_events import StreamDeltaEvent, StreamedResponseEvent, StreamEndEvent
from yoreni.bus.queue import MessageBus
from yoreni.config.network import NetworkConfig
from yoreni.config.schema import AgentDefaults, MemoryConfig, ModelPresetConfig
from yoreni.events import NO_EVENTS, EventSink
from yoreni.llm_usage.context import source_from_request
from yoreni.profile.store import UserProfileStore, profile_store_for_config
from yoreni.providers.base import LLMProvider, ProviderConversationState
from yoreni.providers.factory import ProviderSnapshot
from yoreni.runtime_admission import RuntimeMaintenanceError
from yoreni.runtime_context import (
    RuntimeContextBlock,
    RuntimeContextProvider,
    resolve_runtime_context,
    runtime_context_blocks_from_metadata,
)
from yoreni.security.workspace_access import (
    WorkspaceScopeResolver,
    bind_workspace_scope,
    reset_workspace_scope,
)
from yoreni.utils.llm_runtime import LLMRuntime

if TYPE_CHECKING:
    from yoreni.config.schema import (
        ChannelsConfig,
        Config,
        ProvidersConfig,
        ToolsConfig,
    )
    from yoreni.cron.service import CronService
    from yoreni.triggers.local_store import LocalTriggerStore
    from yoreni.widgets.service import WidgetService


@dataclasses.dataclass
class _Interaction:
    """The user turn currently consuming new messages from one conversation."""

    run_id: str
    channel: str
    chat_id: str
    injections: TurnInjections


class AgentLoop:
    """Own shared infrastructure; persistent identity and Run state live in AgentStore."""

    @property
    def tool_names(self) -> list[str]:
        return self.tools.tool_names


    @property
    def provider(self) -> LLMProvider:
        """Provider selected for future turn admissions."""
        return self.runtime_resolver.runtime.provider


    @property
    def model(self) -> str:
        """Model selected for future turn admissions."""
        return self.runtime_resolver.runtime.model


    @property
    def context_window_tokens(self) -> int:
        """Context limit selected for future turn admissions."""
        return self.runtime_resolver.runtime.context_window_tokens


    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]:
        """Configured model presets exposed for selection and display."""
        return self.runtime_resolver.model_presets


    @property
    def model_preset(self) -> str | None:
        return self.runtime_resolver.model_preset

    @model_preset.setter
    def model_preset(self, name: str | None) -> None:
        self.set_model_preset(name)

    def llm_runtime(self) -> LLMRuntime:
        """Resolve the immutable default used to admit the next turn."""
        previous = self.runtime_resolver.runtime
        runtime = self.runtime_resolver.admit()
        if (
            runtime.model != previous.model
            or runtime.model_preset != previous.model_preset
            or runtime.snapshot_signature != previous.snapshot_signature
        ):
            self._publish_runtime_selection(runtime)
        return runtime

    def dream_runtime(self) -> LLMRuntime | None:
        """Resolve the optional preset used for Dream without changing defaults."""
        if not self.dream_model_preset:
            return None
        return self.runtime_resolver.resolve_preset(self.dream_model_preset)

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        context_window_tokens: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        cron_service: CronService | None = None,
        widget_service: WidgetService | None = None,
        restrict_to_workspace: bool = False,
        tool_registry: ToolRegistry | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        network_config: NetworkConfig | None = None,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        model_presets: dict[str, ModelPresetConfig] | None = None,
        preset_catalog_loader: preset_helpers.PresetCatalogLoader | None = None,
        model_preset: str | None = None,
        dream_model_preset: str | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        turn_delivery_factory: TurnDeliveryFactory | None = None,
        runtime_model_publisher: Callable[[str, str | None], None] | None = None,
        restart_mode: str = "auto",
        local_trigger_store: LocalTriggerStore | None = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
        profile_store: UserProfileStore | None = None,
    ):
        from yoreni.config.schema import ToolsConfig

        _tc = tools_config or ToolsConfig()
        memory_cfg = memory_config or MemoryConfig()
        defaults = AgentDefaults()
        self.bus = bus
        if turn_delivery_factory is not None:
            if turn_delivery_factory.bus is not bus:
                raise ValueError("turn delivery factory must use the agent message bus")
            self.turn_delivery_factory = turn_delivery_factory
        else:
            self.turn_delivery_factory = TurnDeliveryFactory(bus)
        self.runtime_event_publisher = self.turn_delivery_factory.runtime_event_publisher
        self.channels_config = channels_config
        self.restart_mode = restart_mode
        self._runtime_model_publisher = runtime_model_publisher
        self.workspace = workspace
        initial_model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        initial_context_window = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        configured_presets = model_presets or {}
        self.runtime_resolver = ModelRuntimeResolver(
            LLMRuntime.capture(
                provider,
                initial_model,
                context_window_tokens=initial_context_window,
                snapshot_signature=provider_signature,
            ),
            model_presets=configured_presets,
            preset_catalog_loader=preset_catalog_loader,
            configured_default_preset=model_preset,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
        )
        self.dream_model_preset = dream_model_preset
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.tools_config = _tc
        self.network_config = network_config or NetworkConfig()
        self.web_config = _tc.web
        self.exec_config = _tc.exec
        self.cron_service = cron_service
        self.widget_service = widget_service
        self.local_trigger_store = local_trigger_store
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace_scopes = WorkspaceScopeResolver(
            default_workspace=workspace,
            default_restrict_to_workspace=restrict_to_workspace,
        )
        self._start_time = time.time()
        self._extra_hooks: list[AgentHook] = hooks or []
        self._hook_factories: list[AgentTurnHookFactory] = hook_factories or []

        self.context = ContextBuilder(
            workspace,
            timezone=timezone,
            disabled_skills=disabled_skills,
            core_token_budget=memory_cfg.core_token_budget,
            profile_store=profile_store,
        )
        from yoreni.memory.service import MemoryService

        self.memory = MemoryService(
            workspace,
            memory_cfg,
            providers=providers_config,
            core=self.context.core,
        )
        self.tools = tool_registry if tool_registry is not None else ToolRegistry()
        self._provider_snapshot_loader = provider_snapshot_loader
        self._tool_loader = None
        self._runtime_control = None
        self._delivery_callback: Callable[[OutboundMessage], Any] | None = None
        self._exec_session_manager = ExecSessionManager()
        self.runner = AgentRunner()
        from yoreni.agent.persistent import PersistentAgent

        self.agent = PersistentAgent(self)
        self.consolidator = Consolidator()
        self._running = False
        self._runtime_context_providers: list[RuntimeContextProvider] = []
        self._active_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        # One live inbox per conversation; other chats keep their own turn.
        self._interactions: dict[tuple[str, str], _Interaction] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._accepted_runs: set[str] = set()
        self._close_lock = asyncio.Lock()
        self._preserve_inflight_turns_on_shutdown = False
        if model_preset:
            self.set_model_preset(model_preset, publish_update=False)
        self._register_default_tools(provider_snapshot_loader=provider_snapshot_loader)


    @classmethod
    def from_config(
        cls,
        config: Config,
        bus: MessageBus | None = None,
        *,
        tool_registry: ToolRegistry,
        **extra: Any,
    ) -> AgentLoop:
        """Create an AgentLoop from config with the common parameter set.

        The tool registry is caller-owned so application composition can share
        it with infrastructure such as an ``MCPProvider``.

        Extra keyword arguments are forwarded to ``AgentLoop.__init__``,
        allowing callers to override or extend the standard config-derived
        parameters (e.g. ``cron_service``).
        """
        from yoreni.providers.factory import make_provider

        if bus is None:
            bus = MessageBus()
        defaults = config.agents.defaults
        provider = extra.pop("provider", None) or make_provider(config)
        resolved = config.resolve_preset()
        model = extra.pop("model", None) or resolved.model
        context_window_tokens = extra.pop("context_window_tokens", None) or resolved.context_window_tokens
        provider_snapshot_loader = extra.pop("provider_snapshot_loader", None)
        profile_store = extra.pop("profile_store", None) or profile_store_for_config(config)
        preset_snapshot_loader = extra.pop("preset_snapshot_loader", None) or preset_helpers.make_preset_snapshot_loader(
            config,
            provider_snapshot_loader,
        )
        return cls(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            max_iterations=defaults.max_tool_iterations,
            context_window_tokens=context_window_tokens,
            max_tool_result_chars=defaults.max_tool_result_chars,
            provider_retry_mode=defaults.provider_retry_mode,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            channels_config=config.channels,
            timezone=defaults.timezone,
            disabled_skills=defaults.disabled_skills,
            tools_config=config.tools,
            network_config=config.network,
            memory_config=config.memory,
            providers_config=config.providers,
            model_presets=preset_helpers.configured_model_presets(config),
            model_preset=defaults.model_preset,
            dream_model_preset=defaults.dream.model_override,
            restart_mode=config.gateway.restart_mode,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
            tool_registry=tool_registry,
            profile_store=profile_store,
            **extra,
        )


    def set_runtime_max_iterations(self, value: int) -> None:
        self.max_iterations = value

    def invalidate_runtime_config(self) -> None:
        """Invalidate runtime config for lazy refresh at the next admission."""
        self.runtime_resolver.invalidate()


    def refresh_runtime_config(self) -> LLMRuntime:
        """Refresh runtime config now and publish the canonical selection."""
        self.runtime_resolver.invalidate()
        runtime = self.runtime_resolver.admit()
        self._publish_runtime_selection(runtime)
        return runtime


    def _publish_runtime_selection(
        self,
        runtime: LLMRuntime,
        *,
        publish_update: bool = True,
    ) -> None:
        if not publish_update:
            return
        if self._runtime_model_publisher is not None:
            self._runtime_model_publisher(runtime.model, runtime.model_preset)
        self.runtime_event_publisher.runtime_model_changed(
            runtime.model,
            runtime.model_preset,
        )


    def set_model_preset(
        self,
        name: str | None,
        *,
        publish_update: bool = True,
    ) -> LLMRuntime:
        """Select a named default runtime for future turns."""
        old_model = self.model
        runtime = self.runtime_resolver.select_preset(name)
        self._publish_runtime_selection(runtime, publish_update=publish_update)
        logger.info(
            "Runtime model switched for next turn: {} -> {}",
            old_model,
            runtime.model,
        )
        return runtime


    def set_runtime_model(self, model: str) -> LLMRuntime:
        """Select a model on the current provider for future turns."""
        return self.runtime_resolver.select_model(model)


    def set_runtime_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Select a context limit for future turns."""
        return self.runtime_resolver.select_context_window(context_window_tokens)


    def _register_default_tools(
        self,
        *,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None,
        reload: bool = False,
        rebuild: bool = True,
    ) -> None:
        """Register the default set of tools via plugin loader."""
        from yoreni.agent.tools.context import ToolContext
        from yoreni.agent.tools.loader import ToolLoader

        if self._runtime_control is None:
            self._runtime_control = AgentRuntimeControl(self)
            self.register_runtime_context_provider(
                self._runtime_control.workspace_context
            )
        if not rebuild:
            return
        if self._tool_loader is None:
            self._tool_loader = ToolLoader()
        ctx = ToolContext(
            config=self.tools_config,
            network=self.network_config,
            workspace=str(self.workspace),
            bus=self.bus,
            cron_service=self.cron_service,
            exec_session_manager=self._exec_session_manager,
            agent_store=self.agent.store,
            memory=self.memory,
            provider_snapshot_loader=provider_snapshot_loader,
            timezone=self.context.timezone or "UTC",
            workspace_sandbox=self.workspace_scopes.sandbox_status,
            runtime_control=self._runtime_control,
            widget_service=self.widget_service,
        )
        registered = self._tool_loader.load(
            ctx,
            self.tools,
            replace_owned=reload,
        )
        self._bind_delivery_tools()

        logger.info("Registered {} tools: {}", len(registered), registered)

    def set_delivery_callback(
        self,
        callback: Callable[[OutboundMessage], Any],
    ) -> None:
        """Give rebuilt chat tools the gateway's channel delivery path."""
        self._delivery_callback = callback
        self._bind_delivery_tools()

    def _bind_delivery_tools(self) -> None:
        if self._delivery_callback is None:
            return
        from yoreni.agent.tools.chat_delivery import OutboundChatTool

        for name in ("send_message", "rich_content_message"):
            tool = self.tools.get(name)
            if isinstance(tool, OutboundChatTool):
                tool.set_send_callback(self._delivery_callback)

    def reload_runtime(self, config: Config, *, rebuild_tools: bool = True) -> None:
        """Apply a freshly loaded config to the live agent without a restart."""
        defaults = config.agents.defaults
        self.memory.reload_config(config.memory, providers=config.providers)
        self.tools_config = config.tools
        self.web_config = config.tools.web
        self.exec_config = config.tools.exec
        self.network_config = config.network
        self.max_iterations = defaults.max_tool_iterations
        self.max_tool_result_chars = defaults.max_tool_result_chars
        self.provider_retry_mode = defaults.provider_retry_mode
        self.restart_mode = config.gateway.restart_mode
        self.dream_model_preset = defaults.dream.model_override
        self.restrict_to_workspace = config.tools.restrict_to_workspace
        self.workspace_scopes = dataclasses.replace(
            self.workspace_scopes,
            default_restrict_to_workspace=self.restrict_to_workspace,
        )
        self.context.timezone = defaults.timezone
        self._register_default_tools(
            provider_snapshot_loader=self._provider_snapshot_loader,
            reload=True,
            rebuild=rebuild_tools,
        )
        self.runtime_resolver.invalidate()


    def register_runtime_context_provider(
        self,
        provider: RuntimeContextProvider,
    ) -> Callable[[], None]:
        """Register a per-turn context provider and return an unsubscribe callback."""
        if provider in self._runtime_context_providers:
            return lambda: None
        self._runtime_context_providers.append(provider)

        def _unsubscribe() -> None:
            with suppress(ValueError):
                self._runtime_context_providers.remove(provider)

        return _unsubscribe


    async def _resolve_runtime_context_for_request(
        self,
        request: RequestContext,
        tools: ToolRegistry,
    ) -> list[RuntimeContextBlock]:
        providers = [
            *tools.get_runtime_context_providers(),
            *self._runtime_context_providers,
        ]
        blocks = runtime_context_blocks_from_metadata(request.metadata)
        blocks.extend(await resolve_runtime_context(providers, request))
        text = request.original_user_text or ""
        skill_context = (
            await self.runner.prepare_prompt(self.context.skills.build_explicit_skill_runtime_context, text)
            if "$" in text else None
        )
        if skill_context is not None and skill_context not in blocks:
            blocks.append(skill_context)
        return blocks


    async def agent_runtime(self) -> LLMRuntime:
        identity = await asyncio.to_thread(self.agent.store.identity)
        preset = identity.state.get("model_preset")
        return self.runtime_resolver.resolve_preset(preset) if isinstance(preset, str) else self.llm_runtime()

    async def select_agent_model(self, name: str) -> LLMRuntime:
        runtime = self.runtime_resolver.resolve_preset(name)
        identity = await asyncio.to_thread(self.agent.store.identity)
        await asyncio.to_thread(self.agent.store.update, identity.revision, {**identity.state, "model_preset": name})
        self._publish_runtime_selection(runtime)
        return runtime

    async def _run_agent_loop(
        self,
        transcript_input: TranscriptInput,
        events: EventSink = NO_EVENTS,
        streaming: bool = False,
        *,
        runtime: LLMRuntime,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        turn_scopes: list[AbstractContextManager[Any]] | None = None,
        tools: ToolRegistry | None = None,
        request_context: RequestContext,
        provider_state: ProviderConversationState | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        file_states: FileStates | None = None,
        continuation_callback: ContinuationCallback | None = None,
        required_tool_round: RequiredToolRound | None = None,
        interrupt: asyncio.Event | None = None,
        injections: TurnInjections | None = None,
        stream_answers: bool = True,
    ) -> AgentRunResult:
        request = request_context
        scope = request.workspace_scope or self.workspace_scopes.for_turn(
            channel=request.channel, message_metadata=request.metadata,
        )
        tools = tools if tools is not None else self.tools
        file_token = bind_file_states(file_states if file_states is not None else FileStates())
        request_token = bind_request_context(request)
        workspace_token = bind_workspace_scope(scope)

        tool_evidence = None
        if request.turn_id and self.memory.config.enabled:
            active_run = request.turn_id

            async def record_tool_evidence(
                tool_name: str, content: str | list[dict[str, Any]],
            ) -> None:
                await asyncio.to_thread(
                    self.memory.record_tool_result, active_run, tool_name, content,
                )

            tool_evidence = record_tool_evidence

        try:
            with ExitStack() as stack:
                stack.enter_context(GoalInputScope())
                for turn_scope in turn_scopes or ():
                    stack.enter_context(turn_scope)
                hook = build_agent_turn_hook(AgentTurnHookSpec(
                    events=events, streaming=streaming,
                    channel=request.channel, chat_id=request.chat_id,
                    message_id=request.message_id, metadata=request.metadata,
                    attributes=dict(request.attributes), run_id=request.turn_id,
                    workspace=scope.project_path,
                    registered_hook_factories=self._hook_factories,
                    turn_hook_factories=list(hook_factories or []),
                    registered_hooks=self._extra_hooks, turn_hooks=list(hooks or []),
                    ephemeral=ephemeral, run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
                    log_content=request.log_content, stream_answers=stream_answers,
                ))
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=None,
                    tools=tools, runtime=runtime,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    transcript_input=transcript_input,
                    transcript_builder=partial(
                        self.context.build_transcript, channel=request.channel,
                        workspace=scope.project_path,
                    ),
                    hook=hook, concurrent_tools=True,
                    workspace=None if ephemeral else scope.project_path,
                    run_id=request.turn_id,
                    provider_retry_mode=self.provider_retry_mode,
                    checkpoint_callback=checkpoint_callback,
                    checkpoint_messages=checkpoint_callback is not None,
                    record_tool_result=tool_evidence,
                    consolidate_history=partial(
                        self.consolidator.summarize_transcript, runtime=runtime,
                        run_id=request.turn_id, tools=tools.get_definitions(),
                    ),
                    consolidate_provider_compaction=partial(
                        self.consolidator.summarize_provider_compaction, runtime=runtime,
                        run_id=request.turn_id, tools=tools.get_definitions(),
                    ),
                    continuation_callback=continuation_callback,
                    required_tool_round=required_tool_round,
                    interrupt=interrupt,
                    injection_callback=injections.drain if injections is not None else None,
                    terminal_injection_callback=(
                        injections.drain if injections is not None else None
                    ),
                    provider_state=provider_state,
                    llm_usage_source=source_from_request(
                        channel=request.channel, metadata=request.metadata,
                    ),
                    events=events,
                ))
        finally:
            reset_workspace_scope(workspace_token)
            reset_request_context(request_token)
            reset_file_states(file_token)
        if result.stop_reason == "max_iterations" and events.publish is not None and streaming:
            await events.publish(StreamDeltaEvent(content=result.pending_stream_content or result.final_content or ""))
            await events.publish(StreamEndEvent())
        return result

    async def process_event(self, event: AgentInput, **options: Any) -> OutboundMessage:
        with self.bus.admission.activity():
            task = asyncio.current_task()
            key = uuid4().hex if event.ephemeral else await asyncio.to_thread(self.agent.store.admit, event)
            if task is not None:
                self._active_tasks.setdefault(key, set()).add(task)
            try:
                return await self.agent.execute(event, execution_id=key, **options)
            finally:
                if task is not None:
                    self._active_tasks[key].discard(task)
                    if not self._active_tasks[key]:
                        del self._active_tasks[key]

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(self._active_tasks)

    async def cancel_run(self, run_id: str) -> int:
        tasks = tuple(self._active_tasks.get(run_id, ()))
        current = asyncio.current_task()
        targets = [task for task in tasks if task is not current and not task.done()]
        for task in targets:
            task.cancel()
        if targets:
            await asyncio.gather(*targets, return_exceptions=True)
            await asyncio.to_thread(self.agent.store.interrupt, run_id, "Cancelled by owner", cancelled=True)
        return len(targets)

    async def process_direct(
        self, content: str, *, channel: str = "cli", chat_id: str = "direct",
        media: list[str] | None = None, ephemeral: bool = False,
        **options: Any,
    ) -> OutboundMessage:
        return await self.process_event(AgentInput(
            source=channel, content=content, principal="owner", channel=channel,
            chat_id=chat_id, media=tuple(media or ()), ephemeral=ephemeral,
        ), **options)

    async def accept_message(self, msg: InboundMessage) -> str | None:
        """Validate owner input, then either break into the live turn or start one."""
        if msg.channel not in {"cli", "websocket"} and msg.principal != "owner":
            raise PermissionError("External input requires explicit owner authorization.")
        with self.bus.admission.activity():
            event = self.agent.input_from_message(msg)
            if event.kind == "user":
                run_id = await self._insert_message(event, msg)
                if run_id is not None:
                    return run_id
            return await self._start_run(event, msg)

    async def _insert_message(self, event: AgentInput, msg: InboundMessage) -> str | None:
        """Deliver a message into the turn that is already running."""
        active = self._interactions.get((msg.channel, msg.chat_id))
        if active is None:
            return None
        payload = {
            "id": event.event_id,
            "content": event.content,
            "media": list(event.media),
            "created_at_ms": int(time.time() * 1000),
            "metadata": dict(event.metadata),
        }
        await asyncio.to_thread(self.agent.store.append_entry, active.run_id, "user", payload)
        if not active.injections.try_push(event):
            # The turn finished while this message was being recorded; it
            # becomes an ordinary Run and owns its own conversation row.
            await asyncio.to_thread(self.agent.store.remove_entry, active.run_id, event.event_id)
            return None
        await self.runtime_event_publisher.user_input_accepted(
            dataclasses.replace(msg, metadata={**msg.metadata, "run_id": active.run_id}),
            active.run_id,
            message_id=event.event_id,
        )
        return active.run_id

    async def _start_run(self, event: AgentInput, msg: InboundMessage) -> str | None:
        """Admit one Event and schedule its Run, with an inbox while it runs."""
        run_id = await asyncio.to_thread(self.agent.store.admit, event)
        record = await asyncio.to_thread(self.agent.store.read_run, run_id)
        if record["status"] != "queued" or run_id in self._accepted_runs:
            return run_id
        self._accepted_runs.add(run_id)
        await self.runtime_event_publisher.user_input_accepted(
            dataclasses.replace(msg, metadata={**msg.metadata, "run_id": run_id}), run_id,
            message_id=event.event_id if event.kind == "user" else None,
        )
        injections = TurnInjections() if event.kind == "user" else None

        async def execute() -> None:
            interaction = None
            if injections is not None:
                interaction = _Interaction(run_id, msg.channel, msg.chat_id, injections)
                self._interactions[(msg.channel, msg.chat_id)] = interaction
            try:
                await self.agent.submit_message(event, msg, injections=injections)
            finally:
                self._accepted_runs.discard(run_id)
                if interaction is None:
                    return
                key = (msg.channel, msg.chat_id)
                if self._interactions.get(key) is interaction:
                    del self._interactions[key]
                for leftover in interaction.injections.close():
                    await self._resume_leftover(run_id, leftover)

        self.schedule_background(execute())
        return run_id

    async def _resume_leftover(self, run_id: str, event: AgentInput) -> None:
        """Turn a message that missed the live turn into its own Run."""
        await asyncio.to_thread(self.agent.store.remove_entry, run_id, event.event_id)
        await self._start_run(event, InboundMessage(
            channel=event.channel,
            sender_id=event.principal,
            chat_id=event.chat_id,
            content=event.content,
            media=list(event.media),
            metadata=event.metadata,
            principal=cast(Literal["owner", "untrusted"], event.principal),
            input_role="user" if event.kind == "user" else "system",
        ))

    async def _dispatch_inbound(self, msg: InboundMessage) -> None:
        await self.accept_message(msg)

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        self._authorize_automation(msg, "_cron_trigger")
        return await self.agent.submit_message(self.agent.input_from_message(msg), msg)

    async def submit_local_trigger_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        self._authorize_automation(msg, "_local_trigger")
        return await self.agent.submit_message(self.agent.input_from_message(msg), msg)

    def _authorize_automation(self, msg: InboundMessage, key: str) -> None:
        trigger = msg.metadata.get(key)
        if not isinstance(trigger, dict) or cast(dict[str, Any], trigger).get("agent_id") != self.agent.store.identity().id:
            raise PermissionError("Automation does not belong to this Agent.")

    async def run(self) -> None:
        self._running = True
        try:
            while self._running:
                try:
                    async with asyncio.timeout(1):
                        msg = await self.bus.consume_inbound()
                except TimeoutError:
                    continue
                try:
                    await self._dispatch_inbound(msg)
                except Exception:
                    logger.exception("Input rejected")
        finally:
            await self.aclose()

    def maintenance(self) -> AbstractContextManager[None]:
        return self.bus.admission.pause_if_idle(self._has_pending_work)

    def _has_pending_work(self) -> bool:
        return bool(self.bus.inbound_size or self.bus.has_pending_events or self._active_tasks
                    or self._background_tasks or self._exec_session_manager.has_pending_work)

    def preserve_inflight_turns_on_shutdown(self) -> None:
        self._preserve_inflight_turns_on_shutdown = True

    async def aclose(self) -> None:
        async with self._close_lock:
            current = asyncio.current_task()
            tasks = ({task for group in self._active_tasks.values() for task in group} | self._background_tasks) - {current}
            for task in tasks:
                task.cancel()
            errors: list[BaseException] = []
            try:
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for cleanup in (self._exec_session_manager.close_all, self.runtime_resolver.aclose):
                    try:
                        await cleanup()
                    except BaseException as exc:
                        errors.append(exc)
            if errors:
                raise BaseExceptionGroup("Failed to close Agent resources", errors)

    def _background_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.opt(exception=exception).error(
                "Background task '{}' failed",
                task.get_name(),
            )


    def schedule_background(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        try:
            self.bus.admission.check()
        except RuntimeMaintenanceError:
            coro.close()
            raise
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)


    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")


    def _assemble_outbound(
        self,
        msg: InboundMessage,
        final_content: str,
        stop_reason: str,
        streamed_content: bool,
        *,
        turn_latency_ms: int | None = None,
    ) -> OutboundMessage | None:
        """Assemble the final outbound message from turn results."""
        event = None
        meta = dict(msg.metadata or {})
        if streamed_content and stop_reason not in {"error", "tool_error"}:
            event = StreamedResponseEvent()
        if turn_latency_ms is not None:
            meta["latency_ms"] = int(turn_latency_ms)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            event=event,
            metadata=meta,
        )
