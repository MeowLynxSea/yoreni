"""Composition helpers for the embedded WebUI gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger as default_logger

from yoreni.agent.state import AgentStore
from yoreni.channels.websocket.attachment_http import AttachmentHTTP
from yoreni.channels.websocket.attachment_store import AttachmentStore
from yoreni.config.loader import get_config_path
from yoreni.config.paths import get_media_dir
from yoreni.webui.gateway_endpoint import WebUIGatewayEndpoint
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ingress_policy import DEFAULT_WEBUI_INGRESS_POLICY, WebUIIngressPolicy
from yoreni.webui.media_gateway import WebUIMediaGateway
from yoreni.webui.settings_services import WebUISettingsServices
from yoreni.webui.workspaces import WebUIWorkspaceController
from yoreni.webui.ws_http import GatewayHTTPHandler

if TYPE_CHECKING:
    from yoreni.bus.events import InboundMessage
    from yoreni.bus.queue import MessageBus
    from yoreni.channels.websocket.runtime import WebSocketConfig
    from yoreni.cron.service import CronService
    from yoreni.triggers.local_store import LocalTriggerStore


@dataclass(frozen=True)
class GatewayServices:
    """Explicit dependencies shared by WebSocket transport and HTTP routes."""

    http: GatewayHTTPHandler
    endpoint: WebUIGatewayEndpoint
    settings: WebUISettingsServices
    tokens: GatewayTokenStore
    media: WebUIMediaGateway
    uploads: AttachmentHTTP
    ingress: WebUIIngressPolicy
    workspaces: WebUIWorkspaceController
    cron_service: CronService | None
    local_trigger_store: LocalTriggerStore | None
    accept_input: Callable[[InboundMessage], Awaitable[str | None]] | None
    agent_store: AgentStore


def build_gateway_services(
    *,
    config: WebSocketConfig,
    bus: MessageBus,
    static_dist_path: Path | None,
    workspace_path: Path,
    default_restrict_to_workspace: bool,
    config_path: Path | None = None,
    runtime_model_name: Callable[[], str | None] | None,
    refresh_runtime_config: Callable[[], None] | None = None,
    runtime_surface: str,
    runtime_capabilities_overrides: dict[str, Any] | None,
    disabled_skills: set[str] | None = None,
    cron_service: CronService | None = None,
    local_trigger_store: LocalTriggerStore | None = None,
    channel_feature_action: Callable[..., Any] | None = None,
    channel_runtime_status: Callable[[], dict[str, Any]] | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    mcp_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    image_generation_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    skill_state_action: Callable[[set[str]], None] | None = None,
    accept_input: Callable[[InboundMessage], Awaitable[str | None]] | None = None,
    agent_action: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    logger: Any = default_logger,
) -> GatewayServices:
    settings = WebUISettingsServices.create(config_path or get_config_path(), refresh_runtime_config=refresh_runtime_config)
    tokens = GatewayTokenStore()
    ingress = DEFAULT_WEBUI_INGRESS_POLICY
    minimum_frame_bytes = ingress.minimum_full_policy_frame_bytes()
    if config.max_message_bytes < minimum_frame_bytes:
        logger.warning(
            "WebSocket maxMessageBytes={} is below the WebUI ingress policy capacity={}; "
            "policy-valid messages may still hit the transport frame guard",
            config.max_message_bytes,
            minimum_frame_bytes,
        )
    media = WebUIMediaGateway(
        workspace_path=workspace_path,
        logger=logger,
    )
    uploads = AttachmentHTTP(AttachmentStore(
        get_media_dir("websocket"), limits=ingress.attachments,
    ))
    workspaces = WebUIWorkspaceController(
        default_workspace=workspace_path,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )
    http = GatewayHTTPHandler(
        config=config,
        static_dist_path=static_dist_path,
        runtime_model_name=runtime_model_name,
        runtime_surface=runtime_surface,
        runtime_capabilities_overrides=runtime_capabilities_overrides,
        bus=bus,
        tokens=tokens,
        media=media,
        ingress=ingress,
        workspaces=workspaces,
        settings=settings,
        skills_workspace_path=workspace_path,
        disabled_skills=disabled_skills,
        cron_service=cron_service,
        local_trigger_store=local_trigger_store,
        channel_feature_action=channel_feature_action,
        channel_runtime_status=channel_runtime_status,
        mcp_runtime_status=mcp_runtime_status,
        mcp_reload=mcp_reload,
        image_generation_reload=image_generation_reload,
        skill_state_action=skill_state_action,
        agent_action=agent_action,
        log=logger,
    )
    endpoint = WebUIGatewayEndpoint(config=config, http=http, tokens=tokens)
    return GatewayServices(
        http=http,
        endpoint=endpoint,
        settings=settings,
        tokens=tokens,
        media=media,
        uploads=uploads,
        ingress=ingress,
        workspaces=workspaces,
        cron_service=cron_service,
        local_trigger_store=local_trigger_store,
        accept_input=accept_input,
        agent_store=AgentStore(workspace_path),
    )
