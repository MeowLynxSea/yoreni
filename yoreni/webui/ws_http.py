"""HTTP API handler extracted from WebSocketChannel.

Handles all non-WebSocket HTTP routes: bootstrap, Agent actions, settings,
media, sidebar state, static file serving, and token management.

Also houses shared HTTP utility functions used by both this module and
``websocket.py`` to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

from loguru import logger
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from yoreni.agent.goal_state import goal_state_ws_blob
from yoreni.agent.persistent import recoverable_runs
from yoreni.agent.state import AgentStore
from yoreni.cron.events import is_bound_cron_job
from yoreni.cron.types import CronJob, CronSchedule
from yoreni.projects.scope import GLOBAL_SCOPE
from yoreni.projects.service import ProjectError, ProjectService
from yoreni.runtime_admission import RuntimeMaintenanceError
from yoreni.security.workspace_access import WorkspaceScopeError
from yoreni.triggers.local_types import LocalTrigger
from yoreni.webui.automation_results import cron_run_response, trigger_run_response
from yoreni.webui.automations import all_automations_payload
from yoreni.webui.chat_search import (
    normalize_search_query,
    search_conversation,
    semantic_candidates,
    semantic_hit_payloads,
)
from yoreni.webui.file_preview import (
    WebUIFilePreviewError,
    file_preview_payload,
)
from yoreni.webui.gateway_tokens import GatewayTokenStore, token_response_payload
from yoreni.webui.home_widgets import (
    home_widget_layout_payload,
    update_home_widget_layout,
)
from yoreni.webui.http_utils import accepts_gzip as _accepts_gzip
from yoreni.webui.http_utils import (
    case_insensitive_header as _case_insensitive_header,
)
from yoreni.webui.http_utils import (
    combined_list_header as _combined_list_header,
)
from yoreni.webui.http_utils import (
    host_for_url as _host_for_url,
)
from yoreni.webui.http_utils import (
    http_error as _http_error,
)
from yoreni.webui.http_utils import (
    http_json_response as _http_json_response,
)
from yoreni.webui.http_utils import (
    http_response as _http_response,
)
from yoreni.webui.http_utils import (
    is_local_browser_request as _is_local_browser_request,
)
from yoreni.webui.http_utils import (
    is_trusted_proxy_authenticated_request as _is_trusted_proxy_authenticated_request,
)
from yoreni.webui.http_utils import (
    issue_route_secret_matches as _issue_route_secret_matches,
)
from yoreni.webui.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from yoreni.webui.http_utils import (
    normalize_origin as _normalize_origin,
)
from yoreni.webui.http_utils import (
    parse_query as _parse_query,
)
from yoreni.webui.http_utils import (
    parse_request_path as _parse_request_path,
)
from yoreni.webui.http_utils import (
    query_first as _query_first,
)
from yoreni.webui.http_utils import (
    request_origin as _request_origin,
)
from yoreni.webui.http_utils import (
    request_secret as _request_secret,
)
from yoreni.webui.http_utils import (
    safe_host_header as _safe_host_header,
)
from yoreni.webui.ingress_policy import WebUIIngressPolicy
from yoreni.webui.media_gateway import WebUIMediaGateway
from yoreni.webui.onboarding_api import OnboardingError, OnboardingService
from yoreni.webui.outbound_wire import (
    encode_history_run as _encode_history_run,
)
from yoreni.webui.passkeys import PasskeyError, PasskeyService
from yoreni.webui.skills_api import (
    SkillManagementError,
    delete_webui_skill,
    set_webui_skill_enabled,
    webui_skill_detail_payload,
    webui_skills_payload,
)
from yoreni.webui.skills_marketplace import (
    SkillsMarketplaceError,
    install_marketplace_skill,
    marketplace_skill_trends,
    search_marketplace_skills,
    trending_marketplace_skills,
)
from yoreni.webui.star_prompt import update_star_prompt
from yoreni.webui.workspace_browser import browse_workspace_directories
from yoreni.webui.workspaces import WebUIWorkspaceController
from yoreni.widgets.types import WidgetError, WidgetNotFoundError

_SLOW_WEBUI_HTTP_LOG_MS = 1_000
_WEBUI_MUTATION_PAYLOAD_ATTR = "_yoreni_webui_mutation_payload"
_WEBUI_MUTATION_REQUEST_ATTR = "_yoreni_webui_mutation_request"
_NO_STORE_HEADERS = [("Cache-Control", "no-store")]







_WEBUI_MUTATION_PATHS = {
    "agent.resume": "/api/agent/resume",
    "agent.cancel": "/api/agent/cancel",
    "agent.dismiss": "/api/agent/dismiss",
    "onboarding.token": "/api/onboarding/token",
    "onboarding.profile": "/api/onboarding/profile",
    "onboarding.step": "/api/onboarding/step",
    "onboarding.complete": "/api/onboarding/complete",
    "onboarding.embedding_test": "/api/onboarding/embedding-test",
    "onboarding.sandbox.resources": "/api/onboarding/sandbox/resources",
    "onboarding.sandbox.install": "/api/onboarding/sandbox/install",
    "onboarding.sandbox.retry": "/api/onboarding/sandbox/retry",
    "profile.update": "/api/profile/update",
    "profile.password": "/api/profile/password",
    "profile.passkey.options": "/api/profile/passkeys/options",
    "profile.passkey.register": "/api/profile/passkeys/register",
    "profile.passkey.remove": "/api/profile/passkeys/remove",
    "automation.enable": "/api/webui/automations/enable",
    "automation.disable": "/api/webui/automations/disable",
    "automation.delete": "/api/webui/automations/delete",
    "automation.run": "/api/webui/automations/run",
    "automation.update": "/api/webui/automations/update",
    "skill.install": "/api/webui/skills/install",
    "skill.update": "/api/webui/skills/update",
    "skill.delete": "/api/webui/skills/delete",
    "star_prompt.claim": "/api/webui/star-prompt/claim",
    "star_prompt.dismiss": "/api/webui/star-prompt/dismiss",
    "credentials.connect": "/api/webui/credentials/connect",
    "credentials.configure_oauth": "/api/webui/credentials/configure-oauth",
    "credentials.update": "/api/webui/credentials/update",
    "credentials.delete": "/api/webui/credentials/delete",
    "credentials.revoke": "/api/webui/credentials/revoke",
    "credentials.submit": "/api/webui/credentials/submit",
    "credentials.cancel": "/api/webui/credentials/cancel",
    "credentials.connect_oauth": "/api/webui/credentials/connect-oauth",
    "credentials.register_connector": "/api/webui/credentials/register-connector",
    "credentials.rotate_key": "/api/webui/credentials/rotate-key",
    "workspace.favorite": "/api/workspaces/favorite",
    "workspace.resolve_project": "/api/workspaces/resolve-project",
    "project.create": "/api/projects/create",
    "project.update": "/api/projects/update",
    "project.delete": "/api/projects/delete",
    "webui.home_widgets.update": "/api/webui/home-widgets",
    "webui.widgets.delete": "/api/webui/widgets/delete",
    "webui.widgets.refresh": "/api/webui/widgets/refresh",
    "memory.backfill.state": "/api/webui/memory-backfill/state",
    "sandbox.resources": "/api/sandbox/resources",
    "sandbox.rebuild": "/api/sandbox/rebuild",
    "settings.agent.update": "/api/settings/update",
    "settings.model_configuration.create": "/api/settings/model-configurations/create",
    "settings.model_configuration.update": "/api/settings/model-configurations/update",
    "settings.model_configuration.delete": "/api/settings/model-configurations/delete",
    "settings.model_configuration.migrate": "/api/settings/model-configurations/migrate",
    "settings.model_call_order.update": "/api/settings/model-call-order/update",
    "settings.provider.update": "/api/settings/provider/update",
    "settings.provider.create": "/api/settings/provider/create",
    "settings.provider.oauth_login": "/api/settings/provider/oauth-login",
    "settings.provider.oauth_complete": "/api/settings/provider/oauth-login/complete",
    "settings.provider.oauth_logout": "/api/settings/provider/oauth-logout",
    "settings.web_search.update": "/api/settings/web-search/update",
    "settings.image_generation.update": "/api/settings/image-generation/update",
    "settings.transcription.update": "/api/settings/transcription/update",
    "settings.memory.update": "/api/settings/memory/update",
    "settings.runtime_config.update": "/api/settings/runtime-config/update",
    "settings.cli_app.install": "/api/settings/cli-apps/install",
    "settings.cli_app.update": "/api/settings/cli-apps/update",
    "settings.cli_app.uninstall": "/api/settings/cli-apps/uninstall",
    "settings.cli_app.test": "/api/settings/cli-apps/test",
    "settings.feature.enable": "/api/settings/yoreni-features/enable",
    "settings.feature.disable": "/api/settings/yoreni-features/disable",
    "settings.channel.validate": "/api/settings/channels/validate",
    "settings.channel.configure": "/api/settings/channels/configure",
    "settings.pairing.approve": "/api/settings/pairing/approve",
    "settings.pairing.deny": "/api/settings/pairing/deny",
    "settings.mcp.enable": "/api/settings/mcp-presets/enable",
    "settings.mcp.disable": "/api/settings/mcp-presets/disable",
    "settings.mcp.remove": "/api/settings/mcp-presets/remove",
    "settings.mcp.test": "/api/settings/mcp-presets/test",
    "settings.mcp.reconnect": "/api/settings/mcp-presets/reconnect",
    "settings.mcp.proxy": "/api/settings/mcp-presets/proxy",
    "settings.mcp.custom": "/api/settings/mcp-presets/custom",
    "settings.mcp.import": "/api/settings/mcp-presets/import",
    "settings.mcp.import_cursor": "/api/settings/mcp-presets/import-cursor",
    "settings.mcp.tools": "/api/settings/mcp-presets/tools",
    "settings.mcp.oauth_start": "/api/settings/mcp-oauth/start",
    "settings.mcp.oauth_complete": "/api/settings/mcp-oauth/complete",
    "settings.mcp.oauth_cancel": "/api/settings/mcp-oauth/cancel",
}

_WEBUI_CHANNEL_CONNECT_ACTIONS = {
    "settings.channel.connect.start": "start",
    "settings.channel.connect.poll": "poll",
    "settings.channel.connect.cancel": "cancel",
}

# Fix for #5190: On Windows, mimetypes.guess_type() reads the registry key
# HKEY_CLASSES_ROOT\.js\Content Type, which is commonly set to 'text/plain'
# because .js is associated with Windows Script Host rather than web JavaScript.
# That registry value overrides Python's built-in mapping and causes browsers to
# reject ES module scripts with:
#   Failed to load module script: Expected a JavaScript-or-Wasm module script
#   but the server responded with a MIME type of "text/plain".
# We explicitly register correct MIME types for common web static assets here
# (module-import time) so all callers of mimetypes.guess_type() in this process
# benefit, regardless of host registry configuration.
_MIME_FIXES: dict[str, str] = {
    ".js":    "application/javascript",
    ".mjs":   "application/javascript",
    ".css":   "text/css",
    ".html":  "text/html",
    ".json":  "application/json",
    ".svg":   "image/svg+xml",
    ".wasm":  "application/wasm",
}

for _ext, _ctype in _MIME_FIXES.items():
    mimetypes.add_type(_ctype, _ext, strict=True)


if TYPE_CHECKING:
    from yoreni.bus.queue import MessageBus
    from yoreni.channels.websocket.runtime import WebSocketConfig
    from yoreni.cron.service import CronService
    from yoreni.triggers.local_store import LocalTriggerStore
    from yoreni.webui.settings_services import WebUISettingsServices
    from yoreni.widgets.service import WidgetService
    from yoreni.widgets.types import WidgetSummary



def _mutation_payload(request: WsRequest) -> dict[str, Any] | None:
    payload = getattr(request, _WEBUI_MUTATION_PAYLOAD_ATTR, None)
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, Any], payload)


def _request_query(request: WsRequest) -> dict[str, list[str]]:
    payload = _mutation_payload(request)
    if payload is None:
        return _parse_query(request.path)
    query: dict[str, list[str]] = {}
    for key, value in payload.items():
        if not key:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif value is None:
            text = ""
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            text = str(value)
        query[key] = [text]
    return query


def _widget_summary_payload(summary: WidgetSummary) -> dict[str, Any]:
    """Serialize one widget without exposing its server-side workspace path."""
    definition = summary.definition
    return {
        "id": definition.id,
        "title": definition.title,
        "description": definition.description,
        "view": definition.view,
        "has_command": bool(definition.command),
        "every_seconds": definition.every_seconds,
        "has_data": summary.has_data,
        "updated_at_ms": summary.updated_at_ms,
        "next_run_at_ms": summary.next_run_at_ms,
        "error": summary.error,
    }


def _default_model_name_from_config(config_path: Path | None = None) -> str | None:
    try:
        from yoreni.config.loader import load_config
        model = load_config(config_path).resolve_preset().model.strip()
        return model or None
    except Exception as e:
        logger.debug("bootstrap model_name could not load from config: {}", e)
        return None


def _resolve_bootstrap_model_name(
    runtime_name: Callable[[], str | None] | None,
    config_path: Path | None = None,
) -> str:
    if runtime_name is not None:
        try:
            raw = runtime_name()
        except Exception as e:
            logger.debug("bootstrap runtime model resolver failed: {}", e)
        else:
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped:
                    return stripped
    return _default_model_name_from_config(config_path) or ""


# ---------------------------------------------------------------------------
# GatewayHTTPHandler
# ---------------------------------------------------------------------------




class GatewayHTTPHandler:
    """Handles all HTTP routes served alongside the WebSocket endpoint.

    Routes HTTP requests and delegates stateful work to explicit gateway
    services owned by the composition layer.
    """

    def __init__(
        self,
        *,
        config: WebSocketConfig,
        static_dist_path: Path | None,
        runtime_model_name: Callable[[], str | None] | None,
        runtime_surface: str,
        runtime_capabilities_overrides: dict[str, Any] | None,
        bus: MessageBus,
        tokens: GatewayTokenStore,
        media: WebUIMediaGateway,
        ingress: WebUIIngressPolicy,
        workspaces: WebUIWorkspaceController,
        settings: WebUISettingsServices,
        skills_workspace_path: Path,
        disabled_skills: set[str] | None = None,
        cron_service: CronService | None = None,
        local_trigger_store: LocalTriggerStore | None = None,
        widget_service: WidgetService | None = None,
        channel_feature_action: Callable[..., Any] | None = None,
        channel_runtime_status: Callable[[], dict[str, Any]] | None = None,
        mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
        mcp_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        image_generation_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        skill_state_action: Callable[[set[str]], None] | None = None,
        agent_action: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        chat_search_action: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        memory_backfill_action: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        projects: ProjectService | None = None,
        log: Any = logger,
    ) -> None:
        self.config = config
        self.static_dist_path = static_dist_path
        self.runtime_model_name = runtime_model_name
        self.bus = bus
        self.tokens = tokens
        self.media = media
        self.ingress = ingress
        self.workspaces = workspaces
        self.settings = settings
        self.skills_workspace_path = skills_workspace_path
        self.disabled_skills: set[str] = (
            disabled_skills if disabled_skills is not None else set()
        )
        self.skill_state_action = skill_state_action
        self.agent_action = agent_action
        self.chat_search_action = chat_search_action
        self.memory_backfill_action = memory_backfill_action
        self.projects = projects
        self._skill_install_lock = asyncio.Lock()
        self.cron_service = cron_service
        self.local_trigger_store = local_trigger_store
        self.widget_service = widget_service
        self.channel_runtime_status = channel_runtime_status
        self._log = log
        self._runtime_surface = runtime_surface

        from yoreni.webui.settings_api import runtime_capabilities as _rc
        from yoreni.webui.settings_routes import WebUISettingsRouter

        self._capabilities = _rc(runtime_surface, runtime_capabilities_overrides or {})
        self.onboarding = OnboardingService(
            settings.config,
            workspace=skills_workspace_path,
            live_websocket=config,
        )
        self.passkeys = PasskeyService(settings.config, tokens=tokens)
        self.settings_routes = WebUISettingsRouter(
            settings=settings,
            bus=bus,
            logger=self._log,
            check_api_token=self.check_api_token,
            parse_query=_parse_query,
            json_response=_http_json_response,
            error_response=_http_error,
            runtime_surface=runtime_surface,
            runtime_capabilities=self._capabilities,
            channel_feature_action=channel_feature_action,
            channel_runtime_status=channel_runtime_status,
            mcp_runtime_status=mcp_runtime_status,
            mcp_reload=mcp_reload,
            image_generation_reload=image_generation_reload,
            mcp_oauth_redirect_uri=self._mcp_oauth_redirect_uri,
        )

    def workspace_project_selection_available(self, connection: Any) -> bool:
        """Return whether an authenticated WebUI may submit a server path.

        Project paths are interpreted by the gateway, not by the browser. The
        WebUI route and WebSocket handshake already authenticate the caller,
        so remote clients can use the manual server-path picker as well.
        """
        return True

    # -- Token management ---------------------------------------------------

    def check_api_token(self, request: WsRequest) -> bool:
        if getattr(request, "_yoreni_trusted_proxy_authenticated", False):
            return True
        return self.tokens.check_api_token(request)

    # -- Main dispatch ------------------------------------------------------

    async def dispatch(self, connection: Any, request: WsRequest) -> Any | None:
        """Route an HTTP request. Returns Response or None."""
        got, _ = _parse_request_path(request.path)
        started = time.perf_counter()
        response: Any | None = None
        setattr(
            request,
            "_yoreni_trusted_proxy_authenticated",
            _is_trusted_proxy_authenticated_request(connection, request.headers, self.config),
        )

        try:
            if self._is_webui_mutation_path(got):
                return _http_error(
                    405,
                    "WebUI mutations require an authenticated WebSocket",
                )
            with self.bus.admission.activity():
                response = await self._dispatch_resolved(connection, request, got)
            return response
        except RuntimeMaintenanceError:
            return _http_error(503, "Gateway update is being verified. Retry shortly.")
        finally:
            self._log_slow_http(got, response, started)

    async def dispatch_webui_mutation(
        self,
        connection: Any,
        action: str,
        payload: dict[str, Any],
    ) -> Response:
        """Run one explicitly allowlisted mutation for an authenticated WebUI socket."""
        path = self._webui_mutation_path(action, payload)
        if isinstance(path, Response):
            return path

        source_request = getattr(connection, "request", None)
        source_headers = getattr(source_request, "headers", None)
        if source_headers is None:
            headers = Headers()
        else:
            try:
                headers = Headers(source_headers.raw_items())
            except (AttributeError, TypeError):
                try:
                    headers = Headers(source_headers)
                except TypeError:
                    headers = Headers()
        request = WsRequest(path, headers)
        setattr(request, "_yoreni_trusted_proxy_authenticated", True)
        setattr(request, _WEBUI_MUTATION_REQUEST_ATTR, True)
        setattr(request, _WEBUI_MUTATION_PAYLOAD_ATTR, dict(payload))
        # Route on the bare path; handlers read the query from `request` so a
        # mutation can target a parameterised route such as desktop control.
        resolved, _ = _parse_request_path(path)
        try:
            # WebUI mutation tasks already survive a disconnected browser. Keep
            # their async/threaded writes busy until that operation really ends.
            with self.bus.admission.activity():
                response = await self._dispatch_resolved(connection, request, resolved)
        except RuntimeMaintenanceError:
            return _http_error(503, "Gateway update is being verified. Retry shortly.")
        if isinstance(response, Response):
            return response
        return _http_error(404, "WebUI mutation action not found")

    def _is_webui_mutation_path(self, path: str) -> bool:
        if self.settings_routes.is_mutation_path(path):
            return True
        if re.match(r"^/api/webui/automations/(enable|disable|delete|run|update)$", path):
            return True
        if re.match(
            r"^/api/webui/credentials/(connect|configure-oauth|update|delete|revoke|submit|cancel|connect-oauth|register-connector|rotate-key)$",
            path,
        ):
            return True
        if path in {
            "/api/webui/widgets/delete",
            "/api/webui/widgets/refresh",
            "/api/webui/memory-backfill/state",
        }:
            return True
        if re.match(r"^/api/sandbox/control/(take|release)$", path):
            return True
        if path in {"/api/sandbox/resources", "/api/sandbox/rebuild"}:
            return True
        return path in {
            "/api/agent/resume", "/api/agent/cancel", "/api/agent/dismiss",
            "/api/onboarding/token", "/api/onboarding/profile",
            "/api/onboarding/step", "/api/onboarding/complete",
            "/api/onboarding/embedding-test",
            "/api/onboarding/sandbox/install", "/api/onboarding/sandbox/retry",
            "/api/onboarding/sandbox/resources",
            "/api/profile/update",
            "/api/profile/password",
            "/api/profile/passkeys/options",
            "/api/profile/passkeys/register",
            "/api/profile/passkeys/remove",
            "/api/webui/skills/install",
            "/api/webui/skills/update",
            "/api/webui/skills/delete",
            "/api/webui/star-prompt/claim",
            "/api/webui/star-prompt/dismiss",
            "/api/workspaces/resolve-project",
            "/api/workspaces/favorite",
            "/api/projects/create",
            "/api/projects/update",
            "/api/projects/delete",
        }

    @staticmethod
    def _webui_mutation_path(
        action: str,
        payload: dict[str, Any],
    ) -> str | Response:
        path = _WEBUI_MUTATION_PATHS.get(action)
        if path is not None:
            return path
        if action in {"sandbox.control.take", "sandbox.control.release"}:
            controller = payload.get("controller")
            if not isinstance(controller, str) or re.fullmatch(
                r"nbdk_[A-Za-z0-9_-]{16,128}", controller,
            ) is None:
                return _http_error(400, "invalid desktop controller")
            verb = action.rsplit(".", 1)[-1]
            return f"/api/sandbox/control/{verb}?controller={quote(controller, safe='')}"
        connect_action = _WEBUI_CHANNEL_CONNECT_ACTIONS.get(action)
        if connect_action is not None:
            channel = payload.get("channel")
            if not isinstance(channel, str) or re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}",
                channel,
            ) is None:
                return _http_error(400, "invalid channel name")
            return f"/api/settings/channels/{channel}/connect/{connect_action}"
        return _http_error(404, "unknown WebUI mutation action")

    async def _dispatch_resolved(
        self,
        connection: Any,
        request: WsRequest,
        got: str,
    ) -> Any | None:
        # Token issue endpoint
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue(connection, request)

        # Bootstrap
        if got == "/webui/bootstrap":
            return self._handle_bootstrap(connection, request)
        if got == "/webui/terminal":
            return self._handle_bootstrap(connection, request, terminal_probe=True)

        # Authenticator sign-in for the login screen; these run before the
        # authenticated API guard because the browser has no token yet.
        if got == "/api/auth/status":
            return self._handle_auth_status(request)
        if got == "/api/auth/passkey/options":
            return self._handle_passkey_options(request)
        if got == "/api/auth/passkey/verify":
            return self._handle_passkey_verify(request)

        if (
            got == "/api/onboarding"
            or got.startswith("/api/onboarding/")
            or got == "/api/profile"
            or got.startswith("/api/profile/")
        ):
            if not self.check_api_token(request):
                return _http_error(401, "Unauthorized")
            return await self._handle_setup(request, got)

        if got in {"/api/agent", "/api/agent/resume", "/api/agent/cancel", "/api/agent/dismiss"}:
            if not self.onboarding.is_complete():
                return _http_error(409, "Finish setting up Yoreni before chatting")
            if not self.check_api_token(request):
                return _http_error(401, "Unauthorized")
            store = await asyncio.to_thread(AgentStore, self.skills_workspace_path)
            if got != "/api/agent":
                if self.agent_action is None:
                    return _http_error(503, "Agent runtime unavailable")
                payload = _mutation_payload(request) or {}
                run_id = payload.get("run_id")
                if not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
                    return _http_error(400, "invalid Run ID")
                try:
                    return _http_json_response(await self.agent_action(got.rsplit("/", 1)[-1], payload))
                except KeyError:
                    return _http_error(404, "Run not found")
                except (RuntimeError, ValueError) as exc:
                    return _http_error(409, str(exc))
            identity = await asyncio.to_thread(store.identity)
            raw_before = _query_first(_request_query(request), "before_seq")
            before_seq: int | None = None
            if raw_before:
                try:
                    before_seq = int(raw_before)
                except ValueError:
                    return _http_error(400, "invalid before_seq")
                if before_seq <= 0:
                    return _http_error(400, "invalid before_seq")
            # One conversation per scope: the main chat and each project page
            # read only their own Runs.
            raw_project = _query_first(_request_query(request), "project_id")
            scope = GLOBAL_SCOPE
            if raw_project:
                if self.projects is None:
                    return _http_error(503, "projects are unavailable")
                project = await asyncio.to_thread(self.projects.get, raw_project)
                if project is None:
                    return _http_error(404, "project not found")
                scope = project.scope
            history = await asyncio.to_thread(
                store.webui_history, before_seq=before_seq, scope=scope,
            )
            pending_runs = [
                run for run in await asyncio.to_thread(recoverable_runs, store)
                if run["scope"] == scope
            ]
            sign_media = self.media.sign_or_stage_media_path
            return _http_json_response({
                "id": identity.id, "revision": identity.revision, "state": identity.state,
                "scope": scope,
                "pending_runs": pending_runs,
                "history": [
                    _encode_history_run(
                        run,
                        sign_media=lambda media_path: sign_media(Path(media_path)),
                    )
                    for run in history
                ],
            }, extra_headers=_NO_STORE_HEADERS)

        if got.startswith("/api/sandbox/"):
            # Control changes arrive as WebUI mutations, which only an
            # authenticated socket can issue; plain HTTP needs the API token.
            if not (
                getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False)
                or self.check_api_token(request)
            ):
                return _http_error(401, "Unauthorized")
            return await self._handle_sandbox_route(request, got)

        # Settings routes (delegated)
        if got == "/auth/connectors/callback":
            return await self._handle_connector_oauth_callback(request)
        response = await self.settings_routes.dispatch(connection, request, got)
        if response is not None:
            return response

        # Media routes
        response = await self._dispatch_media_routes(request, got)
        if response is not None:
            return response

        # Automation routes
        response = await self._dispatch_automation_routes(request, got)
        if response is not None:
            return response

        # Misc routes
        response = await self._dispatch_misc_routes(connection, request, got)
        if response is not None:
            return response

        # API 404 (never serve SPA for /api/ routes)
        if got.startswith("/api/"):
            return _http_error(404, "API route not found")

        # Static SPA serving
        if self.static_dist_path is not None:
            response = self._serve_static(
                got,
                accept_encoding=_combined_list_header(request.headers, "Accept-Encoding"),
            )
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    def _log_slow_http(self, path: str, response: Any | None, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms < _SLOW_WEBUI_HTTP_LOG_MS:
            return
        if not (path.startswith("/api/") or path == "/webui/bootstrap"):
            return
        if path.endswith("/webui-thread"):
            return
        status = getattr(response, "status_code", None)
        self._log.warning(
            "slow webui http route path={} status={} duration_ms={}",
            path,
            status if status is not None else "none",
            elapsed_ms,
        )

    # -- Sandbox execution plane -------------------------------------------

    async def _handle_sandbox_route(self, request: WsRequest, path: str) -> Any:
        from yoreni.sandbox.desktop_proxy import get_desktop_proxy
        from yoreni.sandbox.errors import SandboxControlError, SandboxError
        from yoreni.sandbox.gateway import get_sandbox_gateway
        from yoreni.sandbox.resources import read_resources, write_resources

        try:
            gateway = get_sandbox_gateway()
        except SandboxError as exc:
            return _http_error(503, str(exc))

        if path == "/api/sandbox/status":
            status = await gateway.status()
            return _http_json_response(
                {
                    "sandbox": status.as_dict(),
                    # The setup sequence covers a rebuild as well as a first
                    # start, so the WebUI can mask itself while the VM returns.
                    "setup": gateway.progress.snapshot(),
                    "resources": read_resources(gateway.config),
                    "control": gateway.control_snapshot().as_dict(),
                    "desktop": {
                        "enabled": gateway.config.desktop_enabled,
                        "browser": gateway.config.browser_enabled,
                        "viewer_path": "/api/sandbox/desktop/ws",
                        # The viewers match this ratio so no letterboxing shows.
                        "width": gateway.config.display_width,
                        "height": gateway.config.display_height,
                    },
                },
                extra_headers=_NO_STORE_HEADERS,
            )

        if path == "/api/sandbox/resources":
            payload = _mutation_payload(request) or {}
            try:
                # Persisting takes the config file lock, so it leaves the loop.
                saved = await asyncio.to_thread(
                    write_resources,
                    self.settings.config.update,
                    payload.get("cpus"),
                    payload.get("memory_mib"),
                )
            except ValueError as exc:
                return _http_error(400, str(exc))
            return _http_json_response(saved)

        if path == "/api/sandbox/rebuild":
            from yoreni.sandbox.setup import get_sandbox_setup

            try:
                return _http_json_response(await get_sandbox_setup().rebuild())
            except SandboxError as exc:
                return _http_error(409, str(exc))

        if path == "/api/sandbox/desktop/session":
            proxy = get_desktop_proxy()
            ticket = proxy.issue_ticket()
            return _http_json_response(
                {
                    "ticket": ticket,
                    "controller_id": ticket,
                    "path": "/api/sandbox/desktop/ws",
                    "expires_in": gateway.config.desktop_token_ttl_s,
                    "control": gateway.control_snapshot().as_dict(),
                },
                extra_headers=_NO_STORE_HEADERS,
            )

        if path in {"/api/sandbox/control/take", "/api/sandbox/control/release"}:
            controller = _query_first(_request_query(request), "controller") or ""
            proxy = get_desktop_proxy()
            if not controller or not proxy.is_controller(controller):
                return _http_error(403, "Unknown desktop controller")
            try:
                if path.endswith("/take"):
                    snapshot = await gateway.take_control(controller)
                else:
                    snapshot = await gateway.release_control(controller)
            except SandboxControlError as exc:
                return _http_error(409, str(exc))
            return _http_json_response({"control": snapshot.as_dict()})

        return _http_error(404, "Sandbox route not found")

    # -- Token issue --------------------------------------------------------

    def _handle_token_issue(self, connection: Any, request: Any) -> Any:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return connection.respond(401, "Unauthorized")
        else:
            self._log.warning(
                "token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        if not self.tokens.can_issue():
            self._log.error(
                "too many outstanding issued tokens ({}), rejecting issuance",
                len(self.tokens.issued_tokens),
            )
            return _http_json_response(
                {"error": "too many outstanding tokens"},
                status=429,
                extra_headers=_NO_STORE_HEADERS,
            )
        token_value = self.tokens.issue_token(self.config.token_ttl_s)
        return _http_json_response(
            token_response_payload(token_value, self.config.token_ttl_s),
            extra_headers=_NO_STORE_HEADERS,
        )

    # -- Onboarding and profile ---------------------------------------------

    def _handle_auth_status(self, request: WsRequest) -> Response:
        return _http_json_response(
            self.passkeys.status(self._read_origin(request)),
            extra_headers=_NO_STORE_HEADERS,
        )

    def _read_origin(self, request: WsRequest) -> str | None:
        """Resolve the page origin for an authenticator request.

        The gateway HTTP layer parses only GET, and a same-origin GET usually
        omits ``Origin``, so the browser-declared origin is the reliable source.
        It only binds the challenge to the page: a forged value cannot produce
        an assertion, because the browser always signs its real origin in the
        client data and verification compares against it.
        """
        return (
            _normalize_origin(_case_insensitive_header(request.headers, "Origin"))
            or _normalize_origin(_query_first(_request_query(request), "origin"))
            or _request_origin(request.headers)
        )

    def _handle_passkey_options(self, request: WsRequest) -> Response:
        try:
            options = self.passkeys.authentication_options(self._read_origin(request))
        except PasskeyError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(options, extra_headers=_NO_STORE_HEADERS)

    def _handle_passkey_verify(self, request: WsRequest) -> Response:
        query = _request_query(request)
        payload = {
            key: _query_first(query, key) or ""
            for key in ("client_data_json", "authenticator_data", "signature", "credential_id")
        }
        try:
            result = self.passkeys.authenticate(payload, self._read_origin(request))
        except PasskeyError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(result, extra_headers=_NO_STORE_HEADERS)

    async def _handle_setup(self, request: WsRequest, path: str) -> Response:
        """Serve first-run state and the owner profile outside agent execution."""
        payload = _mutation_payload(request) or {}
        origin = _request_origin(request.headers)
        try:
            if path == "/api/onboarding":
                return _http_json_response(
                    self.onboarding.snapshot(),
                    extra_headers=_NO_STORE_HEADERS,
                )
            if path == "/api/profile":
                return _http_json_response(
                    {"profile": self.onboarding.read_profile()},
                    extra_headers=_NO_STORE_HEADERS,
                )
            if path == "/api/profile/security":
                return _http_json_response(
                    self.passkeys.list(self._read_origin(request)),
                    extra_headers=_NO_STORE_HEADERS,
                )
            if path == "/api/profile/password":
                return _http_json_response(
                    self.onboarding.change_password(
                        payload.get("current"), payload.get("password")
                    )
                )
            if path == "/api/profile/passkeys/options":
                return _http_json_response(
                    self.passkeys.registration_options(origin, payload.get("name"))
                )
            if path == "/api/profile/passkeys/register":
                return _http_json_response(
                    {"authenticator": self.passkeys.register(payload, origin)}
                )
            if path == "/api/profile/passkeys/remove":
                return _http_json_response(
                    self.passkeys.remove(origin, payload.get("id"))
                )
            if path == "/api/onboarding/token":
                return _http_json_response(self.onboarding.set_token(payload.get("token")))
            if path == "/api/onboarding/profile":
                profile = self.onboarding.save_profile(payload.get("profile"))
                step = payload.get("step")
                if step is not None:
                    self.onboarding.save_step(step)
                return _http_json_response({"profile": profile})
            if path == "/api/onboarding/step":
                return _http_json_response(self.onboarding.save_step(payload.get("step")))
            if path == "/api/onboarding/complete":
                return _http_json_response(self.onboarding.complete(payload.get("locale")))
            if path == "/api/onboarding/sandbox/resources":
                return _http_json_response(
                    self.onboarding.set_sandbox_resources(payload)
                )
            if path == "/api/onboarding/sandbox/install":
                return _http_json_response(await self.onboarding.prepare_sandbox())
            if path == "/api/onboarding/sandbox/retry":
                return _http_json_response(
                    await self.onboarding.prepare_sandbox(retry=True)
                )
            if path == "/api/onboarding/embedding-test":
                return _http_json_response(await self.onboarding.test_embedding(payload))
            if path == "/api/profile/update":
                profile = self.onboarding.update_profile(payload.get("profile"))
                return _http_json_response({"profile": profile})
        except (OnboardingError, PasskeyError) as exc:
            return _http_error(exc.status, exc.message)
        return _http_error(404, "Setup route not found")

    # -- Bootstrap ----------------------------------------------------------

    def _handle_bootstrap(
        self, connection: Any, request: Any, *, terminal_probe: bool = False,
    ) -> Response:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        ticket_valid = self.tokens.check_login_ticket(_request_secret(request.headers))
        is_local_browser = _is_local_browser_request(connection, request.headers)
        is_proxy_authenticated = _is_trusted_proxy_authenticated_request(
            connection,
            request.headers,
            self.config,
        )
        if not is_proxy_authenticated:
            if secret:
                if (
                    not _issue_route_secret_matches(request.headers, secret)
                    and not ticket_valid
                ):
                    return _http_error(401, "Unauthorized")
            elif not ticket_valid and not is_local_browser:
                return _http_error(403, "bootstrap is localhost-only")

        from yoreni.webui.client_contract import gateway_identity

        terminal = gateway_identity(self.tokens.instance_id)
        if terminal_probe:
            # Capability probing does not allocate credentials or acquire client leases.
            return _http_json_response(terminal, extra_headers=_NO_STORE_HEADERS)

        if is_proxy_authenticated:
            payload = {
                "ws_path": _normalize_config_path(self.config.path),
                "ws_url": self._bootstrap_ws_url(request),
                "limits": self.ingress.bootstrap_limits(
                    max_frame_bytes=self.config.max_message_bytes,
                ),
                "model_name": _resolve_bootstrap_model_name(
                    self.runtime_model_name,
                    self.settings.config.path,
                ),
                "runtime_surface": self._runtime_surface,
                "runtime_capabilities": self._capabilities,
                "terminal": terminal,
            }
            return _http_json_response(payload, extra_headers=_NO_STORE_HEADERS)

        api_token_allowed = bool(secret) or is_local_browser or ticket_valid
        if not self.tokens.can_issue(include_api_token=api_token_allowed):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
                extra_headers=_NO_STORE_HEADERS,
            )
        token = self.tokens.issue_token(self.config.token_ttl_s, audience="webui")
        api_token = (
            self.tokens.issue_api_token(self.config.token_ttl_s)
            if api_token_allowed
            else None
        )

        ws_url = self._bootstrap_ws_url(request)
        expected_path = _normalize_config_path(self.config.path)
        payload = {
            "token": token,
            "terminal": terminal,
            "ws_path": expected_path,
            "ws_url": ws_url,
            "expires_in": self.config.token_ttl_s,
            "limits": self.ingress.bootstrap_limits(
                max_frame_bytes=self.config.max_message_bytes,
            ),
            "model_name": _resolve_bootstrap_model_name(
                self.runtime_model_name,
                self.settings.config.path,
            ),
            "runtime_surface": self._runtime_surface,
            "runtime_capabilities": self._capabilities,
        }
        if api_token is not None:
            payload["api_token"] = api_token
        return _http_json_response(payload, extra_headers=_NO_STORE_HEADERS)

    def _bootstrap_ws_url(self, request: Any) -> str:
        headers = getattr(request, "headers", {}) or {}
        if self.config.public_ws_url:
            return self.config.public_ws_url
        host = _safe_host_header(_case_insensitive_header(headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        proto = _case_insensitive_header(headers, "X-Forwarded-Proto")
        proto = proto.split(",", 1)[0].strip().lower()
        secure = proto in {"https", "wss"} or bool(self.config.ssl_certfile.strip())
        scheme = "wss" if secure else "ws"
        expected_path = _normalize_config_path(self.config.path)
        return f"{scheme}://{host}{expected_path}"

    def _mcp_oauth_redirect_uri(self, request: WsRequest) -> str:
        """Derive the browser callback from the same public origin as WebSocket bootstrap."""
        from yoreni.agent.tools.mcp_oauth import MCP_OAUTH_CALLBACK_PATH

        public_ws_url = urlsplit(self._bootstrap_ws_url(request))
        scheme = "https" if public_ws_url.scheme == "wss" else "http"
        return urlunsplit((scheme, public_ws_url.netloc, MCP_OAUTH_CALLBACK_PATH, "", ""))

    # -- Agent workspace preview --------------------------------------------


















    async def _handle_file_preview(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _request_query(request)
        path = _query_first(query, "path") or ""
        try:
            payload = await file_preview_payload(path, scope=self.workspaces.default_scope())
        except WebUIFilePreviewError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)


    # -- Automation routes --------------------------------------------------

    async def _dispatch_automation_routes(
        self,
        request: WsRequest,
        got: str,
    ) -> Response | None:
        if got == "/api/webui/automations":
            return self._handle_webui_automations(request)
        if got == "/api/webui/automations/result":
            return await self._handle_webui_automation_result(request)
        m = re.match(r"^/api/webui/automations/(enable|disable|delete|run|update)$", got)
        if m:
            return await self._handle_webui_automation_action(request, m.group(1))
        return None




    async def _handle_webui_goal(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        store = await asyncio.to_thread(AgentStore, self.skills_workspace_path)
        identity = await asyncio.to_thread(store.identity)
        return _http_json_response(
            {"goal": goal_state_ws_blob(identity.state)},
            extra_headers=_NO_STORE_HEADERS,
        )


    async def _handle_memory_backfill(self, request: WsRequest, mutation: bool) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        handler = self.memory_backfill_action
        if handler is None:
            return _http_error(503, "memory updates are unavailable")
        if not mutation:
            return _http_json_response(
                {"memory_backfill": await handler("snapshot", {})},
                extra_headers=_NO_STORE_HEADERS,
            )
        paused = (_query_first(_request_query(request), "paused") or "").strip().lower()
        if paused not in {"true", "false"}:
            return _http_error(400, "paused must be true or false")
        return _http_json_response(
            {"memory_backfill": await handler("pause" if paused == "true" else "resume", {})},
            extra_headers=_NO_STORE_HEADERS,
        )

    def _handle_webui_automations(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            all_automations_payload(
                self.cron_service,
                local_trigger_store=self.local_trigger_store,
            )
        )



    async def _handle_webui_automation_result(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _request_query(request)
        job_id = (_query_first(query, "id") or "").strip()
        try:
            run_at_ms = int(_query_first(query, "run_at_ms") or "")
        except ValueError:
            return _http_error(400, "invalid run timestamp")
        if not job_id or run_at_ms < 0:
            return _http_error(400, "invalid automation run")
        kind = _query_first(query, "kind") or "cron"
        try:
            if kind == "local_trigger":
                trigger = self.local_trigger_store.get(job_id) if self.local_trigger_store else None
                if trigger is None or self.local_trigger_store is None:
                    return _http_error(404, "automation not found")
                deliveries = [item for item in trigger.run_history if item.run_at_ms == run_at_ms]
                if len(deliveries) != 1:
                    return _http_error(404, "run not found")
                response = await asyncio.to_thread(
                    trigger_run_response, self.local_trigger_store.runs_dir, trigger, deliveries[0],
                )
            elif kind == "cron":
                job = self.cron_service.get_job(job_id) if self.cron_service else None
                if job is None or self.cron_service is None:
                    return _http_error(404, "automation not found")
                if job.payload.kind == "system_event":
                    return _http_error(403, "system automation is protected")
                runs = [item for item in job.state.run_history if item.run_at_ms == run_at_ms]
                if len(runs) != 1:
                    return _http_error(404, "run not found")
                response = await asyncio.to_thread(
                    cron_run_response, self.cron_service.store_path.parent / "runs", job, runs[0],
                )
            else:
                return _http_error(400, "invalid automation kind")
        except OSError:
            self._log.exception("Could not read automation run result")
            return _http_error(500, "Could not read run result")
        return _http_json_response({"response": response})

    async def _handle_webui_automation_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.cron_service is None and self.local_trigger_store is None:
            return _http_error(503, "automation service unavailable")

        query = _request_query(request)
        job_id = (_query_first(query, "id") or _query_first(query, "job_id") or "").strip()
        if not job_id:
            return _http_error(400, "missing automation id")
        trigger = self.local_trigger_store.get(job_id) if self.local_trigger_store else None
        if trigger is not None:
            return self._handle_local_trigger_action(request, action, trigger)

        if self.cron_service is None:
            return _http_error(404, "automation not found")
        job = self.cron_service.get_job(job_id)
        if job is None:
            return _http_error(404, "automation not found")
        if job.payload.kind == "system_event":
            return _http_error(403, "system automation is protected")
        if action in {"enable", "run"} and not is_bound_cron_job(job):
            return _http_error(409, "automation has no linked chat")

        if action == "enable":
            if self.cron_service.enable_job(job_id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.cron_service.enable_job(job_id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            result = self.cron_service.remove_job(job_id)
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        elif action == "run":
            if not job.enabled:
                return _http_error(409, "automation is disabled")
            task = asyncio.create_task(self.cron_service.run_job(job_id, force=False))
            task.add_done_callback(self._log_automation_run_result)
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_automation_update(values, current_job=job)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            try:
                result = self.cron_service.update_job(job_id, **parsed)
            except ValueError as exc:
                return _http_error(400, str(exc))
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        else:
            return _http_error(404, "unknown automation action")

        return self._handle_webui_automations(request)

    def _handle_local_trigger_action(
        self,
        request: WsRequest,
        action: str,
        trigger: LocalTrigger,
    ) -> Response:
        if self.local_trigger_store is None:
            return _http_error(503, "trigger service unavailable")
        if action == "enable":
            if self.local_trigger_store.enable(trigger.id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.local_trigger_store.enable(trigger.id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            if not self.local_trigger_store.delete(trigger.id):
                return _http_error(404, "automation not found")
        elif action == "run":
            return _http_error(409, "local trigger requires a CLI message")
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_local_trigger_update(values)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            if parsed:
                if self.local_trigger_store.update(trigger.id, **parsed) is None:
                    return _http_error(404, "automation not found")
        else:
            return _http_error(404, "unknown automation action")

        return self._handle_webui_automations(request)

    @staticmethod
    def _log_automation_run_result(task: asyncio.Task[bool]) -> None:
        try:
            ran = task.result()
        except Exception:
            logger.exception("WebUI automation run-now task failed")
            return
        if not ran:
            logger.warning("WebUI automation run-now task did not execute")

    # -- Media routes -------------------------------------------------------

    async def _dispatch_media_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2), request)
        return None

    def _handle_media_fetch(
        self, sig: str, payload: str, request: WsRequest | None = None
    ) -> Response:
        return self.media.serve_signed_media(
            sig,
            payload,
            request=request,
        )

    # -- Misc routes --------------------------------------------------------

    async def _dispatch_misc_routes(
        self, connection: Any, request: WsRequest, got: str
    ) -> Response | None:
        if got == "/api/webui/credentials":
            if not self.check_api_token(request):
                return _http_error(401, "Unauthorized")
            from yoreni.webui.credentials_api import credentials_state

            return _http_json_response(credentials_state(), extra_headers=_NO_STORE_HEADERS)
        if got.startswith("/api/webui/credentials/"):
            return await self._handle_credentials_mutation(connection, request, got)
        if got == "/api/agent/file-preview":
            return await self._handle_file_preview(request)
        if got == "/api/webui/goal":
            return await self._handle_webui_goal(request)
        if got in {"/api/webui/memory-backfill", "/api/webui/memory-backfill/state"}:
            return await self._handle_memory_backfill(request, got.endswith("/state"))
        if got == "/api/webui/home-widgets":
            return self._handle_home_widgets(request)
        if got == "/api/webui/chat-search":
            return await self._handle_chat_search(request)
        if got == "/api/webui/chat-search/semantic":
            return await self._handle_chat_search(request, semantic=True)
        if got == "/api/webui/widgets":
            return self._handle_widgets(request)
        if got in {"/api/webui/widgets/delete", "/api/webui/widgets/refresh"}:
            return await self._handle_widget_mutation(request, got)
        widget_match = re.match(r"^/api/webui/widgets/([A-Za-z0-9_-]{1,64})$", got)
        if widget_match:
            return self._handle_widget_detail(request, widget_match.group(1))
        if got == "/api/projects":
            return self._handle_projects(request)
        if got in {"/api/projects/create", "/api/projects/update", "/api/projects/delete"}:
            return await self._handle_project_mutation(request, got)
        if got == "/api/workspaces/favorite":
            return self._handle_workspace_favorite(connection, request)
        if got == "/api/workspaces/resolve-project":
            return await self._handle_workspace_resolve_project(connection, request)
        if got == "/api/workspaces/directories":
            return await self._handle_workspace_directories(connection, request)
        if got == "/api/workspaces":
            return self._handle_workspaces(connection, request)
        if got == "/api/webui/skills/search":
            return await self._handle_webui_skills_search(request)
        if got == "/api/webui/skills/trending":
            return await self._handle_webui_skills_trending(request)
        if got == "/api/webui/skills/trends":
            return await self._handle_webui_skill_trends(request)
        if got == "/api/webui/skills/install":
            return await self._handle_webui_skill_install(connection, request)
        if got == "/api/webui/skills/update":
            return await self._handle_webui_skill_update(request)
        if got == "/api/webui/skills/delete":
            return await self._handle_webui_skill_delete(connection, request)
        if got == "/api/webui/skills":
            return await self._handle_webui_skills(request)
        m = re.match(r"^/api/webui/skills/([^/]+)$", got)
        if m:
            return await self._handle_webui_skill_detail(request, m.group(1))
        if got in {"/api/webui/star-prompt/claim", "/api/webui/star-prompt/dismiss"}:
            if not self.check_api_token(request):
                return _http_error(401, "Unauthorized")
            try:
                show = update_star_prompt("claim" if got.endswith("/claim") else "dismiss")
            except (OSError, ValueError, TimeoutError):
                self._log.exception("failed to persist star invitation state")
                return _http_error(500, "failed to save reminder preference")
            return _http_json_response({"show": show})
        return None

    async def _handle_connector_oauth_callback(self, request: WsRequest) -> Response:
        """Complete a connector OAuth flow on the gateway, never in the browser."""
        from yoreni.credentials.oauth import OAuthFlowError, parse_callback_query
        from yoreni.webui.credentials_api import complete_oauth_callback

        query = _request_query(request)
        state = (_query_first(query, "state") or "").strip()
        code = (_query_first(query, "code") or "").strip()
        try:
            if not state or not code:
                state, code = parse_callback_query(
                    "&".join(f"{key}={value}" for key, values in query.items() for value in values)
                )
            await complete_oauth_callback(state, code)
        except OAuthFlowError as exc:
            return self._connector_callback_page(False, str(exc))
        except Exception:
            self._log.exception("connector OAuth callback failed")
            return self._connector_callback_page(False, "Authorization could not be completed")
        return self._connector_callback_page(True, "You can close this window and return to Yoreni.")

    @staticmethod
    def _connector_callback_page(ok: bool, message: str) -> Response:
        import html as _html

        title = "Connected" if ok else "Not connected"
        body = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{_html.escape(title)}</title></head>"
            "<body style='font-family:system-ui;padding:48px;max-width:32rem;margin:auto'>"
            f"<h1>{_html.escape(title)}</h1>"
            f"<p>{_html.escape(message)}</p></body></html>"
        )
        return _http_response(body.encode("utf-8"), status=200 if ok else 400, content_type="text/html; charset=utf-8")

    async def _handle_credentials_mutation(
        self, connection: Any, request: WsRequest, got: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = _mutation_payload(request) or {}
        action = got.rsplit("/", 1)[-1]
        from yoreni.webui import credentials_api as api

        handlers = {
            "connect": api.connect_account,
            "configure-oauth": api.configure_oauth,
            "update": api.update_account,
            "delete": api.delete_account,
            "revoke": api.revoke_account,
            "submit": api.submit_auth_request,
            "cancel": api.cancel_auth_request,
            "connect-oauth": api.start_oauth,
            "register-connector": api.register_connector,
        }
        try:
            if action == "rotate-key":
                result = await asyncio.to_thread(api.rotate_master_key)
            else:
                handler = handlers.get(action)
                if handler is None:
                    return _http_error(404, "Credential action not found")
                result = await asyncio.to_thread(handler, payload)
        except KeyError:
            return _http_error(404, "Credential record not found")
        except ValueError as exc:
            return _http_error(400, str(exc) or "invalid credential request")
        except Exception:
            self._log.exception("credential action failed: %s", action)
            return _http_error(500, "credential action failed")
        _ = connection
        return _http_json_response(result, extra_headers=_NO_STORE_HEADERS)

    async def _handle_chat_search(self, request: WsRequest, *, semantic: bool = False) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        params = _request_query(request)
        try:
            query = normalize_search_query(_query_first(params, "q"))
        except ValueError as exc:
            return _http_error(400, str(exc) or "invalid search request")
        raw_project = _query_first(params, "project_id")
        scope = GLOBAL_SCOPE
        if raw_project:
            if self.projects is None:
                return _http_error(503, "projects are unavailable")
            project = await asyncio.to_thread(self.projects.get, raw_project)
            if project is None:
                return _http_error(404, "project not found")
            scope = project.scope
        store = await asyncio.to_thread(AgentStore, self.skills_workspace_path)
        rows = await asyncio.to_thread(store.conversation_rows, scope=scope)
        exact, fuzzy = await asyncio.to_thread(search_conversation, rows, query)
        if not semantic:
            return _http_json_response(
                {"exact": exact, "fuzzy": fuzzy}, extra_headers=_NO_STORE_HEADERS,
            )
        action = self.chat_search_action
        if action is None:
            return _http_error(503, "chat search is unavailable")
        matched = {hit["id"] for hit in (*exact, *fuzzy)}
        candidates = semantic_candidates(rows, matched)
        if not candidates:
            return _http_json_response({"hits": []}, extra_headers=_NO_STORE_HEADERS)
        try:
            result = await action({"query": query, "messages": candidates})
        except TimeoutError:
            return _http_error(504, "semantic search timed out")
        except RuntimeError as exc:
            return _http_error(502, str(exc) or "semantic search failed")
        except ValueError as exc:
            return _http_error(400, str(exc) or "invalid search request")
        except Exception:
            self._log.exception("semantic chat search failed")
            return _http_error(500, "semantic chat search failed")
        raw_ids = result.get("ids")
        ids = [
            value for value in cast(list[object], raw_ids) if isinstance(value, str)
        ] if isinstance(raw_ids, list) else []
        return _http_json_response(
            {"hits": semantic_hit_payloads(rows, ids, matched)},
            extra_headers=_NO_STORE_HEADERS,
        )

    def _handle_home_widgets(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = _mutation_payload(request)
        if payload is None:
            return _http_json_response(
                home_widget_layout_payload(),
                extra_headers=_NO_STORE_HEADERS,
            )
        try:
            layout = update_home_widget_layout(payload.get("order"), payload.get("widgets"))
        except ValueError as exc:
            return _http_error(400, str(exc))
        except OSError:
            self._log.exception("failed to save home widget layout")
            return _http_error(500, "could not save widget layout")
        return _http_json_response(layout)

    def _handle_widgets(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        service = self.widget_service
        if service is None:
            return _http_error(503, "widgets are unavailable")
        return _http_json_response(
            {"widgets": [_widget_summary_payload(summary) for summary in service.summaries()]},
            extra_headers=_NO_STORE_HEADERS,
        )

    def _handle_widget_detail(self, request: WsRequest, widget_id: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        service = self.widget_service
        if service is None:
            return _http_error(503, "widgets are unavailable")
        try:
            snapshot = service.snapshot(widget_id)
        except WidgetNotFoundError:
            return _http_error(404, "widget not found")
        except WidgetError as exc:
            return _http_error(400, str(exc))
        return _http_json_response(
            {
                "widget": _widget_summary_payload(snapshot.summary),
                "data": snapshot.data,
                "html": snapshot.html,
            },
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_widget_mutation(self, request: WsRequest, got: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        service = self.widget_service
        if service is None:
            return _http_error(503, "widgets are unavailable")
        payload = _mutation_payload(request) or {}
        widget_id = payload.get("widget_id")
        if not isinstance(widget_id, str) or not widget_id:
            return _http_error(400, "widget_id is required")
        try:
            if got.endswith("/delete"):
                await service.delete(widget_id)
                return _http_json_response({"deleted": widget_id})
            result = await service.refresh(widget_id)
        except WidgetNotFoundError:
            return _http_error(404, "widget not found")
        except WidgetError as exc:
            return _http_error(400, str(exc))
        if result.error:
            return _http_error(500, f"widget refresh failed: {result.error}")
        return _http_json_response(
            {
                "widget_id": widget_id,
                "data": result.data,
                "duration_ms": result.duration_ms,
            },
            extra_headers=_NO_STORE_HEADERS,
        )

    # -- projects ------------------------------------------------------------

    def _project_payload(self, project: Any) -> dict[str, Any]:
        assert self.projects is not None
        payload = project.as_dict()
        payload["active_runs"] = len(self.projects.store.active_runs(project.scope))
        return payload

    def _handle_projects(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.projects is None:
            return _http_error(503, "projects are unavailable")
        return _http_json_response(
            {"projects": [self._project_payload(item) for item in self.projects.list()]},
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_project_mutation(self, request: WsRequest, got: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.projects is None:
            return _http_error(503, "projects are unavailable")
        payload = _mutation_payload(request) or {}
        try:
            if got.endswith("/create"):
                project = await asyncio.to_thread(
                    self.projects.create,
                    name=payload.get("name"),
                    description=payload.get("description") or "",
                    emoji=payload.get("emoji") or "",
                )
            elif got.endswith("/update"):
                project = await asyncio.to_thread(
                    self.projects.update,
                    self._project_id(payload),
                    name=payload.get("name"),
                    description=payload.get("description"),
                    emoji=payload.get("emoji"),
                )
            else:
                return _http_json_response(
                    await self.projects.delete(self._project_id(payload)),
                )
        except KeyError:
            return _http_error(404, "project not found")
        except ProjectError as exc:
            return _http_error(400, str(exc))
        except OSError:
            self._log.exception("project storage operation failed")
            return _http_error(500, "could not update projects")
        return _http_json_response({"project": self._project_payload(project)})

    def _project_id(self, payload: dict[str, Any]) -> str:
        raw = payload.get("project_id")
        if not isinstance(raw, str) or not raw.strip():
            raise ProjectError("project_id is required")
        assert self.projects is not None
        if self.projects.get(raw.strip()) is None:
            raise KeyError(raw)
        return raw.strip()

    def _handle_workspaces(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            self.workspaces.payload(
                can_change_project=self.workspace_project_selection_available(connection),
            )
        )

    def _handle_workspace_favorite(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_project_selection_available(connection):
            return _http_error(403, "project selection is unavailable for this connection")
        payload = _mutation_payload(request) or {}
        try:
            favorites = self.workspaces.set_favorite_project(payload.get("path"), payload.get("pinned"))
        except WorkspaceScopeError as exc:
            return _http_error(exc.status, exc.message)
        except OSError:
            return _http_error(500, "could not save favorite folders")
        return _http_json_response({"favorite_projects": favorites})

    async def _handle_workspace_resolve_project(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_project_selection_available(connection):
            return _http_error(403, "project selection is unavailable for this connection")
        payload = _mutation_payload(request) or {}
        try:
            project = self.workspaces.resolve_project(payload.get("path"))
        except WorkspaceScopeError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(project)

    async def _handle_workspace_directories(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_project_selection_available(connection):
            return _http_error(403, "project selection is unavailable for this connection")
        query = _parse_query(request.path)
        try:
            payload = await asyncio.to_thread(
                browse_workspace_directories,
                _query_first(query, "path") or "",
                default_workspace=self.workspaces.default_scope().project_path,
                query=_query_first(query, "q") or "",
                show_hidden=_query_first(query, "hidden") == "1",
                allow_partial=_query_first(query, "partial") == "1",
            )
        except WorkspaceScopeError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(dict(payload))

    async def _load_webui_skills(self) -> dict[str, Any]:
        return await asyncio.to_thread(
            webui_skills_payload,
            self.skills_workspace_path,
            disabled_skills=set(self.disabled_skills),
        )

    async def _handle_webui_skills(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(await self._load_webui_skills())

    async def _handle_webui_skills_search(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        params = _parse_query(request.path)
        query = _query_first(params, "q") or ""
        provider = _query_first(params, "provider") or "all"
        try:
            payload = await search_marketplace_skills(
                query,
                self.skills_workspace_path,
                provider=provider,
            )
        except SkillsMarketplaceError as exc:
            return _http_error(exc.status, exc.message)
        except Exception:
            self._log.exception("skills marketplace search failed")
            return _http_error(500, "skills marketplace search failed")
        return _http_json_response(payload)

    async def _handle_webui_skills_trending(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        provider = _query_first(_parse_query(request.path), "provider") or "all"
        try:
            payload = await trending_marketplace_skills(
                self.skills_workspace_path,
                provider=provider,
            )
        except SkillsMarketplaceError as exc:
            return _http_error(exc.status, exc.message)
        except Exception:
            self._log.exception("skills marketplace trending lookup failed")
            return _http_error(500, "skills marketplace trending lookup failed")
        return _http_json_response(payload)

    async def _handle_webui_skill_trends(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        skill_ids = _parse_query(request.path).get("id", [])
        try:
            payload = await marketplace_skill_trends(skill_ids)
        except Exception:
            self._log.exception("skills.sh trend history lookup failed")
            return _http_error(500, "skills.sh trend history lookup failed")
        return _http_json_response(payload)

    async def _handle_webui_skill_install(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self._allow_webui_package_install(connection, request):
            return _http_error(403, "remote skill installation is disabled")
        if self._skill_install_lock.locked():
            return _http_error(409, "another skill installation is already in progress")

        query = _request_query(request)
        provider = _query_first(query, "provider") or "skills_sh"
        source = _query_first(query, "source") or ""
        skill_id = _query_first(query, "skill") or ""
        version = _query_first(query, "version") or ""
        async with self._skill_install_lock:
            try:
                action = await install_marketplace_skill(
                    source,
                    skill_id,
                    self.skills_workspace_path,
                    provider=provider,
                    version=version,
                )
            except SkillsMarketplaceError as exc:
                return _http_error(exc.status, exc.message)
            except Exception:
                self._log.exception("skill installation failed")
                return _http_error(500, "skill installation failed")
        return _http_json_response({
            **(await self._load_webui_skills()),
            "last_action": action,
        })

    def _allow_webui_package_install(self, connection: Any, request: WsRequest) -> bool:
        if _is_local_browser_request(connection, request.headers):
            return True
        try:
            return bool(
                self.settings.config.load().tools.webui_allow_remote_package_install
            )
        except Exception:
            self._log.exception("failed to load remote package install policy")
            return False

    async def _handle_webui_skill_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _request_query(request)
        name = _query_first(query, "name") or ""
        raw_enabled = (_query_first(query, "enabled") or "").lower()
        if raw_enabled not in {"true", "false"}:
            return _http_error(400, "enabled must be true or false")
        try:
            action = self.settings.config.run_serialized(
                lambda config_path: set_webui_skill_enabled(
                    self.skills_workspace_path,
                    name,
                    enabled=raw_enabled == "true",
                    disabled_skills=self.disabled_skills,
                    config_path=config_path,
                )
            )
        except SkillManagementError as exc:
            return _http_error(exc.status, exc.message)
        self._apply_skill_state()
        return _http_json_response({
            **(await self._load_webui_skills()),
            "last_action": action,
        })

    async def _handle_webui_skill_delete(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not _is_local_browser_request(connection, request.headers):
            return _http_error(403, "remote skill deletion is disabled")
        name = _query_first(_request_query(request), "name") or ""
        try:
            action = self.settings.config.run_serialized(
                lambda config_path: delete_webui_skill(
                    self.skills_workspace_path,
                    name,
                    disabled_skills=self.disabled_skills,
                    config_path=config_path,
                )
            )
        except SkillManagementError as exc:
            return _http_error(exc.status, exc.message)
        self._apply_skill_state()
        return _http_json_response({
            **(await self._load_webui_skills()),
            "last_action": action,
        })

    def _apply_skill_state(self) -> None:
        if self.skill_state_action is not None:
            self.skill_state_action(set(self.disabled_skills))

    async def _handle_webui_skill_detail(self, request: WsRequest, raw_name: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from urllib.parse import unquote

        name = unquote(raw_name)
        if not name or "/" in name or "\\" in name:
            return _http_error(400, "invalid skill name")
        payload = await asyncio.to_thread(
            webui_skill_detail_payload,
            self.skills_workspace_path,
            name,
            disabled_skills=set(self.disabled_skills),
        )
        if payload is None:
            return _http_error(404, "skill not found")
        return _http_json_response(payload)



    # -- Static file serving ------------------------------------------------

    def _serve_static(
        self,
        request_path: str,
        *,
        accept_encoding: str = "",
    ) -> Response | None:
        assert self.static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self.static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self.static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            index = self.static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        utf8_text = ctype.startswith("text/") or ctype in {
            "application/javascript",
            "application/json",
        }
        compressible = utf8_text or ctype == "image/svg+xml"
        response_path = candidate
        extra_headers: list[tuple[str, str]] = []
        if compressible:
            extra_headers.append(("Vary", "Accept-Encoding"))
            gzip_candidate = candidate.with_name(f"{candidate.name}.gz")
            if _accepts_gzip(accept_encoding) and gzip_candidate.is_file():
                response_path = gzip_candidate
                extra_headers.append(("Content-Encoding", "gzip"))
        try:
            body = response_path.read_bytes()
        except OSError as e:
            self._log.warning("static: failed to read {}: {}", response_path, e)
            return _http_error(500, "Internal Server Error")
        if utf8_text:
            ctype = f"{ctype}; charset=utf-8"
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache), *extra_headers],
        )


def _automation_values_from_request(request: WsRequest) -> dict[str, Any] | None:
    payload = _mutation_payload(request)
    if payload is None or "values" not in payload:
        return {}
    values = payload.get("values")
    return cast(dict[str, Any], values) if isinstance(values, dict) else None


def _parse_automation_update(
    values: dict[str, Any],
    *,
    current_job: CronJob | None = None,
) -> dict[str, Any] | str:
    update: dict[str, Any] = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    if "message" in values:
        raw_message = values.get("message")
        if not isinstance(raw_message, str):
            return "message must be a string"
        message = raw_message.strip()
        if not message:
            return "message cannot be empty"
        update["message"] = message
    if "schedule" in values:
        raw_schedule = values.get("schedule")
        if not isinstance(raw_schedule, dict):
            return "schedule must be an object"
        parsed_schedule = _parse_automation_schedule(cast(dict[str, Any], raw_schedule))
        if isinstance(parsed_schedule, str):
            return parsed_schedule
        if current_job is not None and _schedule_matches_job(parsed_schedule, current_job):
            return update
        schedule_error = _validate_automation_schedule(parsed_schedule)
        if schedule_error:
            return schedule_error
        update["schedule"] = parsed_schedule
        update["delete_after_run"] = parsed_schedule.kind == "at"
    return update


def _parse_local_trigger_update(values: dict[str, Any]) -> dict[str, Any] | str:
    update: dict[str, Any] = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    forbidden = [key for key in ("message", "schedule") if key in values]
    if forbidden:
        return "local trigger updates only support name"
    return update


def _parse_automation_schedule(values: dict[str, Any]) -> CronSchedule | str:
    raw_kind = values.get("kind")
    if not isinstance(raw_kind, str):
        return "schedule kind must be a string"
    kind = raw_kind.strip()
    if kind == "every":
        every_ms = _positive_int(values.get("every_ms"))
        if every_ms is None:
            return "every schedule requires positive every_ms"
        return CronSchedule(kind="every", every_ms=every_ms)
    if kind == "cron":
        raw_expr = values.get("expr")
        if not isinstance(raw_expr, str):
            return "cron schedule requires expr"
        expr = raw_expr.strip()
        if not expr:
            return "cron schedule requires expr"
        raw_tz = values.get("tz")
        if raw_tz is not None and not isinstance(raw_tz, str):
            return "cron schedule timezone must be a string"
        tz = raw_tz.strip() if isinstance(raw_tz, str) else ""
        return CronSchedule(kind="cron", expr=expr, tz=tz or None)
    if kind == "at":
        at_ms = _positive_int(values.get("at_ms"))
        if at_ms is None:
            return "one-time schedule requires positive at_ms"
        return CronSchedule(kind="at", at_ms=at_ms)
    return "unknown schedule kind"


def _schedule_matches_job(schedule: CronSchedule, job: CronJob) -> bool:
    current = job.schedule
    if schedule.kind != current.kind:
        return False
    if schedule.kind == "at":
        return schedule.at_ms == current.at_ms
    if schedule.kind == "every":
        return schedule.every_ms == current.every_ms
    if schedule.kind == "cron":
        return (schedule.expr or "") == (current.expr or "") and (
            schedule.tz or None
        ) == (current.tz or None)
    return False


def _validate_automation_schedule(schedule: CronSchedule) -> str | None:
    if schedule.kind == "at":
        if not schedule.at_ms or schedule.at_ms <= int(time.time() * 1000):
            return "one-time schedule must be in the future"
        return None
    if schedule.kind != "cron":
        return None

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from croniter import croniter

        tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
        base = datetime.now(tz=tz)
        croniter(cast(str, schedule.expr), base).get_next(datetime)
    except Exception:
        return "cron schedule is invalid"
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


