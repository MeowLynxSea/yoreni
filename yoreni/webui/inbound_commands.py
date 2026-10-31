"""Authenticated personal Agent input and WebUI mutation requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from loguru import logger
from websockets.asyncio.server import ServerConnection

from yoreni.agent.state import PERSONAL_CHAT_ID
from yoreni.bus.events import INBOUND_META_TURN_ID, InboundMessage
from yoreni.channels.websocket.attachment_policy import validate_attachment_batch
from yoreni.channels.websocket.attachment_store import AttachmentUploadError
from yoreni.projects.scope import PROJECT_ID_METADATA_KEY
from yoreni.runtime_context import WEBUI_QUOTE_METADATA, normalize_webui_quote
from yoreni.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY, WorkspaceScopeError
from yoreni.webui.attachments import inspect_local_attachment
from yoreni.webui.cli_apps_api import normalize_cli_app_mentions
from yoreni.webui.file_preview import WebUIFilePreviewError
from yoreni.webui.gateway_services import GatewayServices
from yoreni.webui.mcp_presets_api import normalize_mcp_preset_mentions
from yoreni.webui.transcription_ws import webui_transcription_event

if TYPE_CHECKING:
    from yoreni.webui.outbound_delivery import WebUIOutboundDelivery

_WEBUI_REQUEST_CACHE_TTL_S = 300
_WEBUI_REQUEST_CACHE_MAX = 256


@dataclass(frozen=True)
class WebUIRequestResult:
    result: Any = None
    status: int | None = None
    message: str | None = None


@dataclass
class WebUIRequestOperation:
    action: str
    payload_digest: bytes
    task: asyncio.Task[WebUIRequestResult]
    completed_at: float | None = None


class WebUICommandTransport(Protocol):
    def is_allowed(self, sender_id: str) -> bool: ...
    def webui_attach(self, connection: ServerConnection, chat_id: str) -> None: ...
    def webui_detach(self, connection: ServerConnection, chat_id: str) -> None: ...
    async def webui_send_event(self, connection: ServerConnection, event: str, **fields: Any) -> None: ...


class WebUICommandRouter:
    def __init__(self, transport: WebUICommandTransport, gateway: GatewayServices, *, outbound: WebUIOutboundDelivery):
        self._transport = transport
        self.gateway = gateway
        self._outbound = outbound
        self._http_router = gateway.http
        self._uploads = gateway.uploads
        self._media = gateway.media
        self._workspaces = gateway.workspaces
        self._webui_connections = gateway.endpoint.webui_connections
        self.request_tasks: dict[tuple[ServerConnection, str], asyncio.Task[None]] = {}
        self.request_operations: dict[str, WebUIRequestOperation] = {}
        self.request_locks: dict[ServerConnection, asyncio.Lock] = {}

    def _project_from_envelope(self, envelope: dict[str, Any]) -> str | None:
        """Validate the project a message was written in, if it names one."""
        raw = envelope.get("project_id")
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("invalid project id")
        project_id = raw.strip()
        if self.gateway.projects is None or self.gateway.projects.get(project_id) is None:
            raise ValueError("unknown project")
        return project_id

    def workspace_project_selection_available(self, connection: ServerConnection) -> bool:
        return self._http_router.workspace_project_selection_available(connection)

    async def send_webui_protocol_error(self, connection: ServerConnection, detail: str) -> None:
        await self._transport.webui_send_event(connection, "error", detail=detail)

    async def cleanup_connection(self, connection: ServerConnection) -> None:
        self._transport.webui_detach(connection, PERSONAL_CHAT_ID)
        self.gateway.endpoint.discard_connection(connection)
        self.discard_request_lock_if_idle(connection)

    async def dispatch(self, connection: ServerConnection, client_id: str, envelope: dict[str, Any]) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self.send_webui_protocol_error(connection, "access_denied")
            return
        kind = envelope.get("type")
        if kind == "subscribe":
            self._transport.webui_attach(connection, PERSONAL_CHAT_ID)
            await self._transport.webui_send_event(connection, "subscribed")
        elif kind == "webui_request":
            await self.start_webui_request(connection, envelope)
        elif kind == "transcribe_audio":
            event, payload = await webui_transcription_event(envelope, config_path=self.gateway.settings.config.path)
            await self._transport.webui_send_event(connection, event, **payload)
        elif kind == "message":
            await self._dispatch_message(connection, client_id, envelope)
        else:
            await self.send_webui_protocol_error(connection, "unknown command")

    async def _dispatch_message(self, connection: ServerConnection, client_id: str, envelope: dict[str, Any]) -> None:
        event_id = envelope.get("event_id")
        if not isinstance(event_id, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", event_id) is None:
            await self.send_webui_protocol_error(connection, "invalid Event ID")
            return
        content = envelope.get("content")
        if not isinstance(content, str) or self.gateway.ingress.validate_text(content) is not None:
            await self.send_webui_protocol_error(connection, "invalid content")
            return
        try:
            digest = hashlib.sha256(json.dumps(
                envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            ).encode()).hexdigest()
            receipt = await asyncio.to_thread(self.gateway.agent_store.find_event, "websocket", event_id)
            if receipt is not None:
                if receipt["payload"]["metadata"].get("_input_digest") != digest:
                    raise ValueError("Event ID was already used for different input")
                self._transport.webui_attach(connection, PERSONAL_CHAT_ID)
                await self._transport.webui_send_event(
                    connection, "accepted", event_id=event_id, run_id=receipt["id"],
                )
                await self._transport.webui_send_event(
                    connection, "run_receipt", event_id=event_id, run_id=receipt["id"],
                    status=receipt["status"], result=receipt["result"],
                )
                return
            scope = await self._workspaces.scope_from_envelope(
                envelope, can_change_project=self.workspace_project_selection_available(connection),
            )
            project_id = self._project_from_envelope(envelope)
            attachments = envelope.get("media", [])
            if not isinstance(attachments, list):
                raise AttachmentUploadError("malformed")
            if len(cast(list[object], attachments)) > self._uploads.store.limits.max_count:
                raise AttachmentUploadError("Too many attachments")
            paths: list[str] = []
            refs: list[str] = []
            owner = self._uploads.owner(connection)
            mimes: list[str] = []
            for raw in cast(list[object], attachments):
                if not isinstance(raw, dict):
                    raise AttachmentUploadError("malformed")
                item = cast(dict[str, object], raw)
                reference, path = item.get("reference"), item.get("path")
                if (reference is None) == (path is None):
                    raise AttachmentUploadError("Expected an upload reference or local path")
                if isinstance(reference, str):
                    upload, = self._uploads.store.inspect([reference], owner=owner)
                    paths.append(str(upload.path))
                    refs.append(reference)
                    mimes.append(upload.mime)
                elif isinstance(path, str):
                    local = await asyncio.to_thread(inspect_local_attachment, path, scope=scope, limits=self._uploads.store.limits)
                    paths.append(local["path"])
                    mimes.append(local["mime"])
                else:
                    raise AttachmentUploadError("malformed")
            validate_attachment_batch(mimes, limits=self._uploads.store.limits)
            if len(set(paths)) != len(paths):
                raise AttachmentUploadError("Duplicate attachments")
            if not content.strip() and not paths:
                raise ValueError("missing content")
            metadata: dict[str, Any] = {
                "webui": True, "_wants_stream": True, INBOUND_META_TURN_ID: event_id,
                WORKSPACE_SCOPE_METADATA_KEY: scope.metadata(),
                "_input_digest": digest,
            }
            if project_id is not None:
                metadata[PROJECT_ID_METADATA_KEY] = project_id
            cli_apps = normalize_cli_app_mentions(envelope.get("cli_apps"))
            if cli_apps:
                metadata["cli_apps"] = cli_apps
            presets = normalize_mcp_preset_mentions(envelope.get("mcp_presets"), config_path=self.gateway.settings.config.path)
            if presets:
                metadata["mcp_presets"] = presets
            quote = normalize_webui_quote(envelope.get("quote"))
            if quote:
                metadata[WEBUI_QUOTE_METADATA] = quote
            if self.gateway.accept_input is None:
                raise RuntimeError("Agent runtime unavailable")
            # Commit validated upload ownership before durable Event admission.
            # Rejected messages leave committed media for explicit data management.
            if refs:
                self._uploads.store.commit(refs, owner=owner)
            if project_id is not None and paths and self.gateway.projects is not None:
                # An attachment sent inside a project belongs to that project
                # directory, not to the shared media store.
                paths = await asyncio.to_thread(
                    self.gateway.projects.ingest_uploads, project_id, paths,
                )
            run_id = await self.gateway.accept_input(InboundMessage(
                channel="websocket", sender_id=client_id, chat_id=PERSONAL_CHAT_ID,
                content=content, media=paths, metadata=metadata,
            ))
            self._transport.webui_attach(connection, PERSONAL_CHAT_ID)
            await self._transport.webui_send_event(connection, "accepted", event_id=event_id, run_id=run_id)
        except (ValueError, RuntimeError, PermissionError, WorkspaceScopeError, WebUIFilePreviewError) as exc:
            await self._transport.webui_send_event(connection, "error", event_id=event_id, detail=str(exc))

    async def start_webui_request(
        self,
        connection: ServerConnection,
        envelope: dict[str, Any],
    ) -> None:
        request_id = envelope.get("request_id")
        if not isinstance(request_id, str) or re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}",
            request_id,
        ) is None:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="invalid webui request_id",
            )
            return
        if connection not in self._webui_connections:
            await self.send_webui_response(
                connection,
                request_id,
                status=403,
                message="access_denied",
            )
            return

        action = envelope.get("action")
        payload = envelope.get("payload")
        if not isinstance(action, str) or re.fullmatch(
            r"[a-z][a-z0-9_.]{0,127}",
            action,
        ) is None:
            await self.send_webui_response(
                connection,
                request_id,
                status=400,
                message="invalid WebUI mutation action",
            )
            return
        if not isinstance(payload, dict):
            await self.send_webui_response(
                connection,
                request_id,
                status=400,
                message="WebUI mutation payload must be an object",
            )
            return

        payload = cast(dict[str, Any], payload)
        if action == "attachment.inspect":
            try:
                scope = await self._workspaces.scope_from_envelope(payload, can_change_project=self.workspace_project_selection_available(connection))
                path = payload.get("path")
                if not isinstance(path, str):
                    raise ValueError("invalid attachment path")
                result = await asyncio.to_thread(inspect_local_attachment, path, scope=scope, limits=self._uploads.store.limits)
                signed = self._media.sign_or_stage_media_path(Path(result["path"]))
                await self.send_webui_response(connection, request_id, result={**result, "url": signed["url"] if signed else ""})
            except (ValueError, WorkspaceScopeError, WebUIFilePreviewError) as exc:
                await self.send_webui_response(connection, request_id, status=getattr(exc, "status", 400), message=str(exc))
            return

        payload_digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).digest()
        self.prune_request_operations()
        operation = self.request_operations.get(request_id)
        is_replay = operation is not None
        if operation is not None and (
            operation.action != action or operation.payload_digest != payload_digest
        ):
            await self.send_webui_response(
                connection,
                request_id,
                status=409,
                message="request_id was already used for a different WebUI mutation",
            )
            return
        if operation is None:
            operation_task = asyncio.create_task(
                self.execute_webui_request(
                    connection,
                    action,
                    payload,
                )
            )
            new_operation = WebUIRequestOperation(
                action=action,
                payload_digest=payload_digest,
                task=operation_task,
            )
            operation = new_operation
            self.request_operations[request_id] = new_operation

            def mark_complete(_task: asyncio.Task[WebUIRequestResult]) -> None:
                current = self.request_operations.get(request_id)
                if current is not new_operation:
                    return
                new_operation.completed_at = time.monotonic()
                self.prune_request_operations()

            operation_task.add_done_callback(mark_complete)

        key = (connection, request_id)
        if key in self.request_tasks:
            return
        delivery_task = asyncio.create_task(
            self.deliver_webui_request(
                connection,
                request_id,
                operation.task,
                sequence=is_replay,
            )
        )
        self.request_tasks[key] = delivery_task


    def prune_request_operations(self) -> None:
        now = time.monotonic()
        for request_id, operation in tuple(self.request_operations.items()):
            if (
                operation.completed_at is not None
                and now - operation.completed_at >= _WEBUI_REQUEST_CACHE_TTL_S
            ):
                self.request_operations.pop(request_id, None)

        completed = sorted(
            (
                (operation.completed_at, request_id)
                for request_id, operation in self.request_operations.items()
                if operation.completed_at is not None
            ),
            key=lambda item: item[0],
        )
        for _, request_id in completed[:-_WEBUI_REQUEST_CACHE_MAX]:
            self.request_operations.pop(request_id, None)


    def discard_request_lock_if_idle(self, connection: ServerConnection) -> None:
        if connection in self._webui_connections:
            return
        if any(task_connection is connection for task_connection, _ in self.request_tasks):
            return
        self.request_locks.pop(connection, None)


    async def deliver_webui_request(
        self,
        connection: ServerConnection,
        request_id: str,
        operation_task: asyncio.Task[WebUIRequestResult],
        *,
        sequence: bool = False,
    ) -> None:
        try:
            if sequence:
                lock = self.request_locks.setdefault(connection, asyncio.Lock())
                async with lock:
                    result = await asyncio.shield(operation_task)
                    await self.send_webui_response(
                        connection,
                        request_id,
                        result=result.result,
                        status=result.status,
                        message=result.message,
                    )
                return
            result = await asyncio.shield(operation_task)
            await self.send_webui_response(
                connection,
                request_id,
                result=result.result,
                status=result.status,
                message=result.message,
            )
        finally:
            self.request_tasks.pop((connection, request_id), None)
            self.discard_request_lock_if_idle(connection)


    async def execute_webui_request(
        self,
        connection: ServerConnection,
        action: str,
        payload: dict[str, Any],
    ) -> WebUIRequestResult:
        try:
            lock = self.request_locks.setdefault(connection, asyncio.Lock())
            async with lock:
                response = await self._http_router.dispatch_webui_mutation(
                    connection,
                    action,
                    payload,
                )
                status = response.status_code
                body = bytes(response.body).decode("utf-8", errors="replace").strip()
                if 200 <= status < 300:
                    try:
                        result = json.loads(body)
                    except json.JSONDecodeError:
                        return WebUIRequestResult(
                            status=502,
                            message="WebUI mutation returned an invalid response",
                        )
                    return WebUIRequestResult(result=result)
                return WebUIRequestResult(
                    status=status,
                    message=body or response.reason_phrase,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WebUI mutation '{}' failed", action)
            return WebUIRequestResult(
                status=500,
                message="WebUI mutation failed",
            )


    async def send_webui_response(
        self,
        connection: ServerConnection,
        request_id: str,
        *,
        result: Any = None,
        status: int | None = None,
        message: str | None = None,
    ) -> None:
        if status is None:
            await self._transport.webui_send_event(
                connection,
                "webui_response",
                request_id=request_id,
                ok=True,
                result=result,
            )
            return
        await self._transport.webui_send_event(
            connection,
            "webui_response",
            request_id=request_id,
            ok=False,
            error={
                "status": status,
                "message": message or "WebUI mutation failed",
            },
        )


    async def close(self) -> None:
        """Cancel command work and release application-owned gateway state."""
        delivery_tasks = tuple(self.request_tasks.values())
        operation_tasks = tuple(operation.task for operation in self.request_operations.values())
        for task in (*delivery_tasks, *operation_tasks):
            task.cancel()
        if delivery_tasks:
            await asyncio.gather(*delivery_tasks, return_exceptions=True)
        if operation_tasks:
            await asyncio.gather(*operation_tasks, return_exceptions=True)
        self.request_tasks.clear()
        self.request_locks.clear()
        self.request_operations.clear()
        await self._http_router.settings_routes.close()
        self.gateway.tokens.clear()
        self.gateway.endpoint.clear()


