"""Foreground gateway runtime and lifecycle helpers."""

import asyncio
import os
import signal
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, cast

import typer
from loguru import logger
from rich.console import Console

from yoreni import __logo__, __version__
from yoreni.agent.hook import AgentHook, AgentRunHookContext
from yoreni.agent.hooks import create_file_edit_activity_hook
from yoreni.agent.loop import AgentLoop
from yoreni.agent.tools.image_generation import (
    register_image_generation_tool,
    reload_image_generation_tool,
)
from yoreni.agent.tools.mcp import MCPProvider
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.bus.events import InboundMessage, OutboundMessage
from yoreni.bus.queue import MessageBus
from yoreni.cli import terminal as cli_terminal
from yoreni.cli.runtime_config import _migrate_cron_store
from yoreni.cli.webui_support import (
    _gateway_health_bind_note,
    _gateway_health_url,
    _host_for_local_browser,
    _launch_browser,
    _prepare_webui_bundle_for_gateway,
    _print_foreground_port_conflict,
    _tcp_endpoint_reachable,
    _webui_browser_url,
    _webui_channel_enabled,
    _webui_display_url,
    _webui_endpoint_reachable,
)
from yoreni.config.paths import get_runtime_subdir, is_default_workspace
from yoreni.config.schema import Config
from yoreni.gateway.runtime import (
    GatewayHealthIdentity,
    GatewayInstance,
    GatewayRuntime,
    instance_id_for,
)
from yoreni.security.network import is_loopback_host
from yoreni.utils.evaluator import evaluate_response, resolve_evaluator_prompt
from yoreni.utils.helpers import sync_workspace_templates
from yoreni.utils.token_encoding import warmup_token_encoding
from yoreni.webui.build import BuildMode
from yoreni.webui.dev import WebUIDevError, WebUIDevServer

__all__ = ["_run_gateway"]

console = Console()


async def _deliver_to_channel(msg: OutboundMessage, *, bus: MessageBus) -> None:
    """Deliver a tool result through the shared authenticated channel dispatcher."""
    await bus.publish_outbound(msg)


class _MCPReadinessHook(AgentHook):
    """Retry application-owned MCP connections before the runner reads tools."""

    def __init__(self, provider: MCPProvider) -> None:
        super().__init__()
        self._provider = provider

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._provider.connect()


def _http_endpoint_responding(url: str, *, timeout_s: float = 0.25) -> bool:
    """Return whether an HTTP endpoint responds, including with an auth error."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout_s):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False


async def _watch_webui_dev_server(
    server: WebUIDevServer,
    shutdown_event: asyncio.Event,
    *,
    poll_interval_s: float = 0.2,
) -> None:
    """Fail the foreground gateway when its owned Vite sidecar exits."""
    while not shutdown_event.is_set():
        await asyncio.sleep(poll_interval_s)
        if shutdown_event.is_set():
            return
        server.ensure_running()


def _signal_name(signum: int) -> str:
    with suppress(ValueError):
        return signal.Signals(signum).name
    return f"signal {signum}"


def _install_gateway_shutdown_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    tasks: list[asyncio.Task[Any]],
    print_status: Callable[[str], None],
) -> Callable[[], None]:
    """Install foreground gateway signal handlers and return a restore callback."""
    loop_signals: list[int] = []
    previous_handlers: list[tuple[int, Any]] = []
    shutdown_requested = False

    def request_shutdown(signum: int) -> None:
        nonlocal shutdown_requested
        sig_name = _signal_name(signum)
        if shutdown_requested:
            logger.warning("Forcing gateway shutdown after repeated {}", sig_name)
            for task in tasks:
                if not task.done():
                    task.cancel()
            return
        shutdown_requested = True
        logger.info("Gateway shutdown requested by {}", sig_name)
        print_status("\nShutting down... Press Ctrl+C again to force.")
        shutdown_event.set()

    shutdown_signals = [signal.SIGINT, signal.SIGTERM]
    if sigbreak := getattr(signal, "SIGBREAK", None):
        shutdown_signals.append(sigbreak)

    for signum in shutdown_signals:
        try:
            loop.add_signal_handler(signum, request_shutdown, signum)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, lambda sig, _frame: request_shutdown(sig))
            except (RuntimeError, ValueError):
                logger.debug("Could not install gateway handler for {}", _signal_name(signum))
                continue
            previous_handlers.append((signum, previous))
        else:
            loop_signals.append(signum)

    def restore() -> None:
        for signum in loop_signals:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signum)
        for signum, handler in previous_handlers:
            with suppress(RuntimeError, ValueError):
                signal.signal(signum, handler)

    return restore


_HEARTBEAT_PREAMBLE = (
    "[Your response will be delivered directly to the user's messaging app. "
    "Output ONLY the final user-facing message. Never reference internal "
    "files (HEARTBEAT.md, AWARENESS.md, etc.), your instructions, or your "
    "decision process. If nothing needs reporting, respond with just "
    "'All clear.' and nothing else.]\n\n"
)


def _heartbeat_has_active_tasks(content: str) -> bool:
    """True if HEARTBEAT.md has task lines, ignoring headers, blanks and comments."""
    in_comment = False
    in_active_section: bool = False
    for line in content.splitlines():
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if not stripped or stripped.startswith("#"):
            if stripped.startswith("##") and not stripped.startswith("###"):
                heading = stripped.lstrip("#").strip().lower()
                in_active_section = heading.startswith("active tasks")
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        if in_active_section is False:
            continue
        return True
    return False


def _heartbeat_target(enabled_channels: Iterable[str]) -> tuple[str, str]:
    """Personal proactive results go only to the authenticated personal client."""
    from yoreni.agent.state import PERSONAL_CHAT_ID

    return ("websocket", PERSONAL_CHAT_ID) if "websocket" in enabled_channels else ("cli", "direct")


_GATEWAY_HEALTH_MAX_CONNECTIONS = 64
_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS = 2.0


def _print_gateway_health_endpoint(host: str, port: int) -> None:
    """Print a usable health URL and make non-loopback binds explicit."""
    console.print(
        f"[green]✓[/green] Health endpoint: {_gateway_health_url(host, port)}"
        f"{_gateway_health_bind_note(host)}"
    )
    if is_loopback_host(host):
        return

    console.print(
        "[yellow]Warning: the unauthenticated health endpoint is listening beyond loopback "
        "and may be reachable from other devices. "
        f"Keep port {port} private or protect it with a firewall or reverse proxy.[/yellow]"
    )


def _gateway_readiness_payload(channels: Any) -> tuple[bool, dict[str, object]]:
    """Describe process liveness separately from required WebSocket readiness."""
    channel_status: dict[str, Any] = {}
    get_status = getattr(channels, "get_status", None)
    if callable(get_status):
        try:
            raw_status = get_status()
            if isinstance(raw_status, dict):
                channel_status = cast(dict[str, Any], raw_status)
        except Exception:
            logger.exception("Gateway readiness could not read channel status")

    websocket = channel_status.get("websocket")
    websocket_required = websocket is not None or "websocket" in getattr(
        channels,
        "enabled_channels",
        (),
    )
    if not websocket_required:
        websocket_state = "disabled"
        ready = True
    elif isinstance(websocket, dict):
        websocket_status = cast(dict[str, Any], websocket)
        ready = websocket_status.get("running") is True
        state = websocket_status.get("state")
        websocket_state = str(state) if isinstance(state, str) else "unavailable"
    else:
        ready = False
        websocket_state = "unavailable"

    return ready, {
        "status": "ok" if ready else "degraded",
        "process": "alive",
        "ready": ready,
        "websocket": websocket_state,
    }


async def _close_gateway_runtime(
    agent: AgentLoop,
    mcp_provider: MCPProvider,
    channels: Any,
    tasks: list[asyncio.Task[Any]],
    runtime_tasks: asyncio.Future[list[Any]] | None,
    *,
    task_wait_timeout: float = 15.0,
    close_timeout: float = 15.0,
) -> None:
    """Cancel runtime tasks, then deterministically close application resources.

    Order matters: runtime tasks (including the agent loop and any in-flight
    turn) are cancelled and awaited -- bounded -- before the loop-owned resources
    and the application-owned MCP provider are torn down. The final close is
    bounded and idempotent, so it also covers a cancelled or incomplete loop
    cleanup without leaving subprocess transports alive past ``loop.close()``.
    """
    # Some SDKs swallow task cancellation while attempting to reconnect.
    # Close channel transports before waiting for their runners to exit.
    await channels.stop_all()
    for task in tasks:
        if not task.done():
            task.cancel()
    pending: set[asyncio.Task[Any]] = set()
    if tasks:
        # Bounded: a coroutine that swallows cancellation (e.g. an SDK reconnect
        # loop) must not hold the stop open until systemd's timeout kills the
        # cgroup. Anything still pending is abandoned and closed underneath.
        _done, pending = await asyncio.wait(tasks, timeout=task_wait_timeout)
        # A task can swallow the first cancellation while unwinding. Re-cancel
        # timed-out tasks so an agent loop stuck draining background work reaches
        # its resource-cleanup phase before the explicit final close below.
        for task in pending:
            task.cancel()
    if runtime_tasks is not None and not runtime_tasks.done():
        runtime_tasks.cancel()
    for label, close in (
        ("agent", agent.aclose),
        ("MCP provider", mcp_provider.aclose),
    ):
        try:
            await asyncio.wait_for(close(), timeout=close_timeout)
        except BaseException as exc:  # noqa: BLE001 - shutdown must proceed
            logger.warning("Gateway shutdown: {} cleanup incomplete: {}", label, exc)
    # Retrieving an already-finished gather prevents noisy unhandled exceptions,
    # but never wait for it here: its children were bounded individually above.
    if runtime_tasks is not None and runtime_tasks.done():
        with suppress(asyncio.CancelledError, Exception):
            await runtime_tasks


def _run_gateway(
    config: Config,
    *,
    port: int | None = None,
    verbose: bool = False,
    open_browser_url: str | None = None,
    open_browser_ready_url: str | None = None,
    webui_static_dist: bool = True,
    webui_bundle_mode: BuildMode = "warn",
    webui_runtime_surface: str = "browser",
    webui_runtime_capabilities: dict[str, Any] | None = None,
    health_server_enabled: bool = True,
    unconfigured_provider_error: str | None = None,
    webui_dev_server: WebUIDevServer | None = None,
    gateway_instance: GatewayInstance | None = None,
) -> None:
    """Claim this instance before initializing any runtime writers."""
    from uuid import uuid4

    from yoreni.config.loader import get_config_path

    port = port if port is not None else config.gateway.port
    instance = gateway_instance or GatewayInstance.resolve(config_path=get_config_path())
    gateway_runtime = GatewayRuntime(paths=instance.paths)
    instance_id = instance_id_for(instance.paths)
    with gateway_runtime.foreground_instance(instance.start_options(port=port, verbose=verbose)):
        _run_claimed_gateway(
            config, port=port, gateway_instance=instance, gateway_runtime=gateway_runtime,
            health_identity=GatewayHealthIdentity(instance_id, uuid4().hex, __version__),
            open_browser_url=open_browser_url, open_browser_ready_url=open_browser_ready_url,
            webui_static_dist=webui_static_dist, webui_bundle_mode=webui_bundle_mode,
            webui_runtime_surface=webui_runtime_surface,
            webui_runtime_capabilities=webui_runtime_capabilities,
            health_server_enabled=health_server_enabled,
            unconfigured_provider_error=unconfigured_provider_error,
            webui_dev_server=webui_dev_server,
        )


def _run_claimed_gateway(
    config: Config,
    *,
    port: int,
    gateway_instance: GatewayInstance,
    gateway_runtime: GatewayRuntime,
    health_identity: GatewayHealthIdentity,
    open_browser_url: str | None,
    open_browser_ready_url: str | None,
    webui_static_dist: bool,
    webui_bundle_mode: BuildMode,
    webui_runtime_surface: str,
    webui_runtime_capabilities: dict[str, Any] | None,
    health_server_enabled: bool,
    unconfigured_provider_error: str | None,
    webui_dev_server: WebUIDevServer | None,
) -> None:
    """Initialize and run only while the caller holds the gateway lifecycle claim."""
    from yoreni.agent.model_presets import load_model_preset_catalog
    from yoreni.agent.tools.chat_delivery import OutboundChatTool
    from yoreni.agent.turn_delivery import TurnDeliveryFactory
    from yoreni.bus.queue import MessageBus
    from yoreni.channels.manager import ChannelManager
    from yoreni.config.watcher import watch_config_file
    from yoreni.cron.bound_runner import run_bound_cron_job
    from yoreni.cron.events import is_bound_cron_job
    from yoreni.cron.service import CronJobSkippedError, CronService
    from yoreni.cron.types import CronJob, CronRunResult
    from yoreni.llm_usage import record_llm_call
    from yoreni.llm_usage.context import llm_usage_source
    from yoreni.profile.store import UserProfileStore
    from yoreni.providers.factory import (
        ProviderSnapshot,
        build_provider_snapshot,
        build_unconfigured_provider_snapshot,
        load_provider_snapshot,
    )
    from yoreni.providers.fallback_provider import FallbackProvider
    from yoreni.triggers.local_runner import run_local_trigger_queue
    from yoreni.triggers.local_store import LocalTriggerStore
    from yoreni.webui.chat_search import semantic_chat_search
    from yoreni.webui.run_events import (
        build_webui_fallback_model_observer,
        connect_run_events,
        publish_widget_change,
    )
    from yoreni.widgets.runner import WidgetRunner
    from yoreni.widgets.service import WidgetService
    from yoreni.widgets.store import WidgetStore

    webui_url = _webui_browser_url(config)
    gateway_host_for_browser = _host_for_local_browser(config.gateway.host)
    if health_server_enabled and _tcp_endpoint_reachable(gateway_host_for_browser, port):
        _print_foreground_port_conflict(
            webui_url=webui_url,
            gateway_host=config.gateway.host,
            gateway_port=port,
        )
        raise typer.Exit(1)
    if _webui_channel_enabled(config) and _webui_endpoint_reachable(webui_url):
        _print_foreground_port_conflict(
            webui_url=webui_url,
            gateway_host=config.gateway.host,
            gateway_port=port,
        )
        raise typer.Exit(1)

    console.print(f"{__logo__} Starting yoreni gateway version {__version__} on port {port}...")
    warmup_token_encoding()
    _prepare_webui_bundle_for_gateway(
        config,
        mode=webui_bundle_mode,
        webui_static_dist=webui_static_dist,
    )
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    fallback_model_observer = build_webui_fallback_model_observer(bus)

    def _observe_provider(snapshot: ProviderSnapshot) -> ProviderSnapshot:
        snapshot.provider.set_llm_call_observer(record_llm_call)
        if isinstance(snapshot.provider, FallbackProvider):
            snapshot.provider.set_fallback_model_observer(fallback_model_observer)
        return snapshot

    def _load_gateway_provider_snapshot(
        *args: Any,
        **kwargs: Any,
    ) -> ProviderSnapshot:
        try:
            return _observe_provider(load_provider_snapshot(*args, **kwargs))
        except ValueError as exc:
            if unconfigured_provider_error is None:
                raise
            return _observe_provider(build_unconfigured_provider_snapshot(config, str(exc)))

    if unconfigured_provider_error is not None:
        provider_snapshot = _observe_provider(
            build_unconfigured_provider_snapshot(config, unconfigured_provider_error)
        )
    else:
        try:
            provider_snapshot = _observe_provider(build_provider_snapshot(config))
        except ValueError as exc:
            console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(1) from exc

    # Use the same runtime identity for foreground and managed gateway processes.
    from yoreni.gateway.runtime import (
        GatewayClientLease,
        monitor_gateway_clients,
    )

    config_path = str(gateway_instance.config_path)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path, admission=bus.admission)
    trigger_store = LocalTriggerStore(config.workspace_path)
    widget_service = WidgetService(
        WidgetStore(get_runtime_subdir("widgets")),
        runner=WidgetRunner(
            sandbox=config.tools.exec.sandbox,
            sandbox_enabled=config.tools.sandbox.enabled,
        ),
        workspace=config.workspace_path,
        admission=bus.admission,
        on_change=partial(publish_widget_change, bus),
    )

    turn_delivery_factory = TurnDeliveryFactory(bus)

    from yoreni.config.paths import get_media_dir
    from yoreni.sandbox import SandboxGateway, set_sandbox_gateway
    from yoreni.security.network import validate_url_target

    def _sandbox_url_guard(url: str) -> None:
        ok, error = validate_url_target(url)
        if not ok:
            raise ValueError(error or "URL is not allowed")

    sandbox_gateway = SandboxGateway(
        config.tools.sandbox,
        host_workspace=str(config.workspace_path),
        host_media_dir=str(get_media_dir()),
        url_guard=_sandbox_url_guard,
    )
    set_sandbox_gateway(sandbox_gateway)

    tools = ToolRegistry()
    register_image_generation_tool(config, tools, workspace=config.workspace_path)
    mcp_provider = MCPProvider.from_config(config, tools)

    # Create agent with cron service
    agent = AgentLoop.from_config(
        config, bus,
        provider=provider_snapshot.provider,
        model=provider_snapshot.model,
        context_window_tokens=provider_snapshot.context_window_tokens,
        cron_service=cron,
        widget_service=widget_service,
        provider_snapshot_loader=_load_gateway_provider_snapshot,
        preset_catalog_loader=load_model_preset_catalog,
        turn_delivery_factory=turn_delivery_factory,
        provider_signature=provider_snapshot.signature,
        local_trigger_store=trigger_store,
        hooks=[_MCPReadinessHook(mcp_provider)],
        hook_factories=[create_file_edit_activity_hook],
        tool_registry=tools,
        profile_store=UserProfileStore(Path(config_path).parent / "profile.json"),
    )

    deliver_to_channel = partial(_deliver_to_channel, bus=bus)

    def _recover_memory_state() -> None:
        from yoreni.memory.dream import DreamConsolidator

        interrupted = DreamConsolidator(agent.memory).recover()
        if interrupted:
            logger.warning(
                "Recovered {} interrupted memory consolidation(s)", interrupted
            )

    if agent.memory.config.enabled:
        _recover_memory_state()

    async def _publish_memory_backfill(state: dict[str, Any]) -> None:
        from yoreni.webui.run_events import publish_memory_backfill

        await publish_memory_backfill(bus, state)

    agent.memory.backfill.on_change = _publish_memory_backfill

    from yoreni.credentials.manager import get_credential_manager
    from yoreni.webui.run_events import publish_auth_request

    _credential_loop: list[asyncio.AbstractEventLoop] = []

    def _on_auth_request(request: Any) -> None:
        payload = request.public_dict()
        if not _credential_loop:
            return

        def _publish() -> None:
            asyncio.ensure_future(publish_auth_request(bus, payload))

        _credential_loop[0].call_soon_threadsafe(_publish)

    get_credential_manager().set_auth_listener(_on_auth_request)

    # Provider keys are not connector credentials, but they must still never
    # reach a model prompt through a config dump or tool echo.
    _credential_manager = get_credential_manager()
    provider_values = list(config.providers.__dict__.values())
    provider_values.extend((config.providers.model_extra or {}).values())
    for provider in provider_values:
        api_key = getattr(provider, "api_key", None)
        if isinstance(api_key, str):
            _credential_manager.register_external_secret(api_key, label="provider")

    async def agent_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = payload["run_id"]
        record = await asyncio.to_thread(agent.agent.store.read_run, run_id)
        if action == "cancel":
            count = await agent.cancel_run(run_id)
            if not count and record["status"] in {"queued", "running", "interrupted"}:
                await asyncio.to_thread(agent.agent.store.interrupt, run_id, "Cancelled by owner", cancelled=True)
            return {"cancelled": count}
        if action == "dismiss":
            if record["status"] not in {"queued", "running", "interrupted"}:
                raise RuntimeError("Run is already settled")
            if run_id in agent.active_run_ids:
                raise RuntimeError("Run is still active")
            await asyncio.to_thread(
                agent.agent.store.interrupt, run_id, "Dismissed by owner", cancelled=True,
            )
            return {"dismissed": True}
        if record["status"] not in {"queued", "running", "interrupted"}:
            raise RuntimeError("Run is already settled")
        if run_id in agent.active_run_ids:
            raise RuntimeError("Run is already active")
        agent.schedule_background(agent.agent.resume(run_id, deliver=True))
        return {"accepted": True}

    async def chat_search_action(payload: dict[str, Any]) -> dict[str, Any]:
        ids = await semantic_chat_search(
            payload.get("query"),
            payload.get("messages"),
            provider=agent.provider,
            model=agent.model,
        )
        return {"ids": ids}

    async def memory_backfill_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        backfill = agent.memory.backfill
        if action == "snapshot":
            return await asyncio.to_thread(backfill.snapshot)
        if action == "pause":
            return await backfill.pause()
        if action == "resume":
            return await backfill.resume()
        raise ValueError(f"unknown memory update action: {action}")

    # Every tool that speaks into a chat needs the channel delivery path, and
    # the heartbeat suppresses all of them so its output goes through the gate.
    def _delivery_tools() -> list[OutboundChatTool]:
        return [
            tool for name in ("send_message", "rich_content_message")
            if isinstance(tool := agent.tools.get(name), OutboundChatTool)
        ]

    agent.set_delivery_callback(deliver_to_channel)

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | CronRunResult | None:
        """Execute a cron job through the agent."""
        async def _silent(*_args: Any, **_kwargs: Any) -> None:
            pass

        # Dream is a framework job: evidence extraction, then consolidation.
        if job.name in {"dream", "dream_backlog"}:
            memory = agent.memory
            if job.name == "dream_backlog":
                stats = await asyncio.to_thread(memory.store.stats)
                threshold = config.memory.dream_backlog_threshold
                if stats.unconsolidated_facts < threshold and not stats.pending_jobs:
                    return None
            try:
                await agent.agent.archive_pending()
                runtime = agent.dream_runtime() or agent.llm_runtime()
                extracted = await memory.extract_pending(
                    runtime, limit=config.memory.extraction_batch,
                )
                embedded = await memory.backfill.step()
                from yoreni.memory.dream import DreamConsolidator

                outcome = await DreamConsolidator(
                    memory, batch_facts=config.memory.dream_batch_facts,
                ).consolidate(runtime)
                stats = await asyncio.to_thread(memory.store.stats)
                logger.info(
                    "Dream {}: {} change(s), extraction={}, embeddings={}, "
                    "backlog facts={}, pending jobs={}, tokens={}",
                    outcome.status, outcome.changes, extracted, embedded,
                    stats.unconsolidated_facts, stats.pending_jobs, stats.last_dream_tokens,
                )
            except Exception:
                logger.exception("Dream cron job failed")
            return None

        # Heartbeat is a system job that checks HEARTBEAT.md for active tasks.
        if job.name == "heartbeat":
            heartbeat_file = config.workspace_path / "HEARTBEAT.md"
            try:
                content = heartbeat_file.read_text(encoding="utf-8")
            except OSError:
                logger.debug("Heartbeat: HEARTBEAT.md missing")
                return None
            if not _heartbeat_has_active_tasks(content):
                logger.debug("Heartbeat: HEARTBEAT.md has no active tasks")
                return None

            channel, chat_id = _heartbeat_target(channels.enabled_channels)

            prompt = (
                _HEARTBEAT_PREAMBLE
                + f"You are executing periodic heartbeat tasks. Read the active tasks below, perform each one, and report what you did:\n\n{content}"
            )

            # Internal check: funnel all output through the post-run gate so the
            # turn can't deliver directly via send_message and skip it.
            suppress_tokens = [
                (tool, tool.set_suppress_delivery(True)) for tool in _delivery_tools()
            ]
            try:
                await mcp_provider.connect()
                from yoreni.agent.state import AgentInput

                resp = await agent.process_event(
                    AgentInput(
                        "heartbeat", prompt, principal="owner", kind="internal",
                        channel=channel, chat_id=chat_id,
                    ),
                    on_progress=_silent,
                )
            finally:
                for suppressed_tool, token in suppress_tokens:
                    suppressed_tool.reset_suppress_delivery(token)

            if not resp or not resp.content:
                return

            response = resp.content

            evaluator_prompt = resolve_evaluator_prompt(config.workspace_path)

            # Fail closed: stay silent on evaluator failure instead of notifying.
            with llm_usage_source("cron"):
                should_notify = await evaluate_response(
                    response=response,
                    task_context=prompt,
                    provider=agent.provider,
                    model=agent.model,
                    evaluator_prompt=evaluator_prompt,
                    default_notify=False,
                )

            if should_notify and channel != "cli":
                logger.info("Heartbeat: completed, delivering response")
                await bus.publish_outbound(
                    OutboundMessage(channel=channel, chat_id=chat_id, content=response),
                )
            else:
                logger.info("Heartbeat: silenced by post-run evaluation")
            return response

        if is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=cron)

        reason = "scheduled task must be bound to the Agent and an output address"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}",
            job.name,
            job.id,
            reason,
        )
        raise CronJobSkippedError(reason)

    cron.on_job = on_cron_job

    def _webui_runtime_model_name() -> str | None:
        return agent.model.strip() or None

    def _webui_refresh_runtime_config() -> None:
        agent.refresh_runtime_config()

    def _system_cron_signature() -> tuple[Any, ...]:
        return (
            config.agents.defaults.dream.model_dump(mode="json"),
            config.agents.defaults.timezone,
            config.memory.dream_backlog_threshold,
            config.memory.dream_backlog_interval_min,
            config.gateway.heartbeat.model_dump(mode="json"),
        )

    def _sync_system_cron_jobs() -> None:
        from yoreni.cron.types import CronJob, CronPayload, CronSchedule

        dream_cfg = config.agents.defaults.dream
        if dream_cfg.enabled:
            cron.register_system_job(CronJob(
                id="dream",
                name="dream",
                schedule=dream_cfg.build_schedule(config.agents.defaults.timezone),
                payload=CronPayload(kind="system_event"),
            ))
            if config.memory.dream_backlog_threshold > 0:
                cron.register_system_job(CronJob(
                    id="dream_backlog",
                    name="dream_backlog",
                    schedule=CronSchedule(
                        kind="every",
                        every_ms=config.memory.dream_backlog_interval_min * 60_000,
                        tz=config.agents.defaults.timezone,
                    ),
                    payload=CronPayload(kind="system_event"),
                ))
            else:
                cron.remove_system_job("dream_backlog")
        else:
            cron.remove_system_job("dream")
            cron.remove_system_job("dream_backlog")

        heartbeat = config.gateway.heartbeat
        if heartbeat.enabled:
            cron.register_system_job(CronJob(
                id="heartbeat",
                name="heartbeat",
                schedule=CronSchedule(
                    kind="every",
                    every_ms=heartbeat.interval_s * 1000,
                    tz=config.agents.defaults.timezone,
                ),
                payload=CronPayload(kind="system_event"),
            ))
        else:
            cron.remove_system_job("heartbeat")

    from yoreni.webui.onboarding_api import OnboardingService
    from yoreni.webui.settings_services import WebUISettingsConfig

    onboarding = OnboardingService(
        WebUISettingsConfig(Path(config_path)),
        workspace=agent.workspace,
    )

    async def _webui_accept_input(message: InboundMessage) -> str | None:
        """Reject chat input until first-run setup is complete."""
        if not onboarding.is_complete():
            raise RuntimeError("Finish setting up Yoreni before chatting")
        return await agent.accept_message(message)

    def _webui_skill_state_action(disabled_skills: set[str]) -> None:
        config.agents.defaults.disabled_skills = sorted(disabled_skills)
        agent.context.skills.disabled_skills = set(disabled_skills)

    async def _webui_image_generation_reload() -> dict[str, Any]:
        return await reload_image_generation_tool(
            config_path=Path(config_path),
            workspace=agent.workspace,
            registry=tools,
        )

    def _agent_proxy() -> str | None:
        try:
            return config.network.resolve_proxy(config.tools.web.use_proxy)
        except ValueError:
            return None

    async def _webui_reload_agent_config() -> None:
        """Apply a saved settings file to the running gateway without a restart."""
        from yoreni.config.loader import load_config, resolve_config_env_vars

        path = Path(config_path)
        fresh = await asyncio.to_thread(
            lambda: resolve_config_env_vars(load_config(path), config_path=path)
        )
        previous_cron = _system_cron_signature()
        previous_proxy = _agent_proxy()
        previous_save_dir = config.tools.image_generation.save_dir
        config.agents = fresh.agents
        config.tools = fresh.tools
        config.network = fresh.network
        config.gateway = fresh.gateway
        config.memory = fresh.memory
        config.providers = fresh.providers
        agent.reload_runtime(config)
        if _system_cron_signature() != previous_cron:
            _sync_system_cron_jobs()
        widget_service.set_sandbox(config.tools.exec.sandbox)
        widget_service.set_sandbox_enabled(config.tools.sandbox.enabled)
        channels.apply_runtime_config(config)
        agent.refresh_runtime_config()
        proxy_changed = _agent_proxy() != previous_proxy
        if proxy_changed or config.tools.image_generation.save_dir != previous_save_dir:
            await _webui_image_generation_reload()
        if proxy_changed:
            try:
                await mcp_provider.reload()
            except Exception:
                logger.exception("MCP reload after proxy change failed")

    # Create the channel manager that serves the embedded WebUI and channel adapters.
    channels = ChannelManager(
        config,
        bus,
        cron_service=cron,
        local_trigger_store=trigger_store,
        widget_service=widget_service,
        webui_runtime_model_name=_webui_runtime_model_name,
        webui_refresh_runtime_config=_webui_refresh_runtime_config,
        webui_reload_agent_config=_webui_reload_agent_config,
        webui_static_dist=webui_static_dist,
        webui_runtime_surface=webui_runtime_surface,
        webui_runtime_capabilities=webui_runtime_capabilities,
        webui_mcp_runtime_status=mcp_provider.runtime_status,
        webui_mcp_reload=mcp_provider.reload,
        webui_image_generation_reload=_webui_image_generation_reload,
        webui_skill_state_action=_webui_skill_state_action,
        webui_agent_action=agent_action,
        webui_chat_search_action=chat_search_action,
        webui_memory_backfill_action=memory_backfill_action,
        webui_projects=agent.projects,
        webui_accept_input=_webui_accept_input,
        config_path=Path(config_path),
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    hb_cfg = config.gateway.heartbeat
    if hb_cfg.enabled:
        console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")
    else:
        console.print("[yellow]✗[/yellow] Heartbeat: disabled")

    async def _health_server(host: str, health_port: int) -> None:
        """Lightweight HTTP health endpoint on the gateway port."""
        import json as _json

        connection_slots = asyncio.Semaphore(_GATEWAY_HEALTH_MAX_CONNECTIONS)

        async def handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            if connection_slots.locked():
                writer.close()
                return

            async with connection_slots:
                try:
                    data = await asyncio.wait_for(
                        reader.read(4096),
                        timeout=_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS,
                    )
                    request_line = data.split(b"\r\n", 1)[0].decode(
                        "utf-8", errors="replace",
                    )
                    method, path = "", ""
                    parts = request_line.split(" ")
                    if len(parts) >= 2:
                        method, path = parts[0], parts[1]

                    if method == "GET" and path == "/health":
                        ready, payload = _gateway_readiness_payload(channels)
                        payload["runtime"] = asdict(health_identity)
                        payload["pid"] = os.getpid()
                        payload["maintenance"] = bus.admission.paused
                        body = _json.dumps(payload)
                        status = "200 OK" if ready else "503 Service Unavailable"
                        content_type = "application/json"
                    else:
                        body = "Not Found"
                        status = "404 Not Found"
                        content_type = "text/plain"

                    resp = (
                        f"HTTP/1.0 {status}\r\n"
                        f"Content-Type: {content_type}\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n"
                        f"\r\n{body}"
                    )
                    writer.write(resp.encode())
                    await writer.drain()
                except (asyncio.TimeoutError, ConnectionError):
                    pass
                finally:
                    writer.close()

        server = await asyncio.start_server(handle, host, health_port)
        _print_gateway_health_endpoint(host, health_port)
        async with server:
            await server.serve_forever()
    # Register Dream and Heartbeat system jobs (idempotent on restart)
    _sync_system_cron_jobs()
    if config.agents.defaults.dream.enabled:
        console.print(
            f"[green]✓[/green] Dream: {config.agents.defaults.dream.describe_schedule()}"
        )
    else:
        console.print("[yellow]○[/yellow] Dream: disabled")

    cron_status = cron.status()
    cron_job_count = cast(int, cron_status["jobs"])
    if cron_job_count > 0:
        console.print(f"[green]✓[/green] Cron: {cron_job_count} scheduled jobs")

    async def _open_browser_when_ready() -> None:
        """Wait for the gateway to bind, then point the user's browser at the webui."""
        if not open_browser_url:
            return
        from urllib.parse import urlparse

        # Channels start asynchronously. When the caller supplies a backend
        # readiness route, wait for an actual HTTP response rather than probing
        # the WebSocket listener with an incomplete TCP connection.
        if open_browser_ready_url:
            for _ in range(40):  # ~4s max per listener
                if await asyncio.to_thread(
                    _http_endpoint_responding,
                    open_browser_ready_url,
                ):
                    break
                await asyncio.sleep(0.1)

        parsed = urlparse(open_browser_url)
        target_host = parsed.hostname or config.gateway.host or "127.0.0.1"
        target_port = parsed.port or port
        for _ in range(40):  # ~4s max
            try:
                _reader, writer = await asyncio.open_connection(
                    target_host,
                    target_port,
                )
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.1)
        display_url = _webui_display_url(open_browser_url)
        try:
            if _launch_browser(open_browser_url):
                console.print(f"[green]✓[/green] Opened browser at {display_url}")
            else:
                console.print(f"[yellow]Could not open browser; visit {display_url}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Could not open browser ({e}); visit {display_url}[/yellow]")

    async def run() -> None:
        _credential_loop.clear()
        _credential_loop.append(asyncio.get_running_loop())
        tasks: list[asyncio.Task[Any]] = []
        shutdown_task: asyncio.Task[Any] | None = None
        runtime_tasks: asyncio.Future[list[Any]] | None = None
        startup_complete = False
        shutdown_event = asyncio.Event()
        cli_terminal._ensure_interactive_tty_mode()
        restore_shutdown_handlers = _install_gateway_shutdown_handlers(
            asyncio.get_running_loop(),
            shutdown_event,
            tasks,
            console.print,
        )
        try:
            if config.tools.sandbox.enabled:
                async def _start_sandbox() -> None:
                    try:
                        await sandbox_gateway.start()
                    except Exception as exc:  # noqa: BLE001 - control plane stays up
                        logger.error(
                            "The Yoreni sandbox VM is unavailable; execution tools "
                            "will fail until it can start: {}",
                            exc,
                        )

                tasks.append(asyncio.create_task(
                    _start_sandbox(),
                    name="yoreni-sandbox-start",
                ))
            # Re-read once on first admission to close the watcher subscription window.
            agent.runtime_resolver.invalidate()
            # Recovery must finish before WebSocket and other channels begin
            # accepting new input.  That makes a new user message reliably
            # supersede an old recoverable turn instead of racing its queue.
            await cron.start()
            await widget_service.start()
            if agent.memory.config.enabled:
                await agent.memory.backfill.start()
            async def _run_agent() -> None:
                try:
                    await bus.admission.wait_until_open()
                    await mcp_provider.connect()
                    await agent.run()
                finally:
                    await mcp_provider.aclose()

            async def _monitor_local_clients() -> None:
                orphaned = await monitor_gateway_clients(
                    GatewayClientLease(gateway_runtime, kind="gateway-monitor"),
                    shutdown_event,
                )
                if orphaned:
                    logger.info("Last local client disappeared; stopping on-demand gateway")

            tasks.extend([
                asyncio.create_task(
                    gateway_runtime.wait_for_shutdown_request(shutdown_event),
                    name="yoreni-gateway-stop-request",
                ),
                asyncio.create_task(
                    watch_config_file(
                        Path(config_path),
                        lambda: agent.invalidate_runtime_config(),
                    ),
                    name="yoreni-config-watcher",
                ),
                asyncio.create_task(_run_agent(), name="yoreni-agent-loop"),
                asyncio.create_task(channels.start_all(), name="yoreni-channels"),
                asyncio.create_task(
                    run_local_trigger_queue(
                        store=trigger_store,
                        submit_turn=agent.submit_local_trigger_turn,
                        is_channel_enabled=lambda name: channels.get_channel(name) is not None,
                        admission=bus.admission,
                    ),
                    name="yoreni-local-triggers",
                ),
                asyncio.create_task(
                    _monitor_local_clients(),
                    name="yoreni-gateway-client-monitor",
                ),
            ])
            if health_server_enabled:
                tasks.append(asyncio.create_task(
                    _health_server(config.gateway.host, port),
                    name="yoreni-health-server",
                ))
            if open_browser_url:
                tasks.append(asyncio.create_task(
                    _open_browser_when_ready(),
                    name="yoreni-open-browser",
                ))
            if webui_dev_server is not None:
                tasks.append(asyncio.create_task(
                    _watch_webui_dev_server(webui_dev_server, shutdown_event),
                    name="yoreni-webui-dev-server",
                ))
            runtime_tasks = asyncio.gather(*tasks)
            startup_complete = True
            shutdown_task = asyncio.create_task(
                shutdown_event.wait(),
                name="yoreni-gateway-shutdown",
            )
            done, _pending = await asyncio.wait(
                {runtime_tasks, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if runtime_tasks in done:
                await runtime_tasks
            else:
                runtime_tasks.cancel()
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except WebUIDevError:
            raise
        except Exception:
            import traceback

            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
            if not startup_complete:
                # Do not report a successful gateway command when startup
                # failed before any runtime task or listener was created.
                raise typer.Exit(1)
        finally:
            try:
                if shutdown_task and not shutdown_task.done():
                    shutdown_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await shutdown_task
                cron.stop()
                await agent.memory.backfill.stop()
                await widget_service.aclose()
                # A gateway exit interrupts ownership of active turns; it is
                # not the same as the user stopping a turn.  Keep checkpoints
                # so the next gateway can offer an explicit Continue action.
                agent.preserve_inflight_turns_on_shutdown()
                agent.stop()
                # Cancel runtime tasks first, then deterministically close
                # exec/MCP resources while the event loop is still alive.
                await _close_gateway_runtime(
                    agent,
                    mcp_provider,
                    channels,
                    tasks,
                    runtime_tasks,
                )
                await sandbox_gateway.close()
                set_sandbox_gateway(None)
                await bus.drain()
            finally:
                restore_shutdown_handlers()

    with connect_run_events(bus):
        if health_server_enabled:
            gateway_runtime.publish_health_host(config.gateway.host)
        asyncio.run(run())
