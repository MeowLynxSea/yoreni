"""Agent-facing connector discovery and authentication requests.

These tools never read a secret. ``request_authentication`` asks the user to
complete a structured auth card; the submitted values travel through the
WebUI's protected channel directly into the credential store.
"""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import json
from typing import Any

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext, current_request_context
from yoreni.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.credentials.auth import AuthRequestStatus
from yoreni.credentials.manager import get_credential_manager
from yoreni.credentials.models import AuthMethod

_MAX_WAIT_SECONDS = 600

_PLACEHOLDER_HINT = (
    "Each placeholder is the credential itself: pass it as the argument value the "
    "tool expects (password, api_key, code, ...) and call that tool now, in this "
    "turn. The gateway replaces it with the real secret at execution. Never ask "
    "the user for the value and never repeat the placeholder to them."
)


def _run_id() -> str | None:
    ctx = current_request_context()
    if ctx is None:
        return None
    return ctx.turn_id


@tool_parameters(
    tool_parameters_schema(
        connector_id=StringSchema(
            "Connector that needs authentication, e.g. api_key, basic_auth, "
            "one_time_code, mcp_server. Call list_connections to see what exists."
        ),
        method=StringSchema(
            "Authentication method: api_key, bearer, basic, oauth2, or one_time_code. "
            "Defaults to the connector's first method."
        ),
        purpose=StringSchema(
            "One sentence shown to the user explaining which service is being connected and why."
        ),
        allowed_tools=ArraySchema(
            StringSchema(""),
            description=(
                "Tool names allowed to receive the resulting credential, e.g. "
                "['browser_type']. Leave empty when the credential is only for redaction."
            ),
        ),
        allowed_domains=ArraySchema(
            StringSchema(""),
            description="Domains this credential may be used against, e.g. ['example.com'].",
        ),
        timeout_seconds=IntegerSchema(
            description="How long to wait for the user, in seconds (30-600). Defaults to 300.",
            minimum=30,
            maximum=600,
        ),
        required=["connector_id", "purpose"],
    )
)
class RequestAuthenticationTool(Tool):
    """Ask the user to connect an account, then resume with its identifier."""

    interruptible = True

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:  # noqa: ARG003
        return cls()

    @property
    def name(self) -> str:
        return "request_authentication"

    @property
    def description(self) -> str:
        return (
            "Ask the user to sign in or enter a credential for an external service. "
            "Use this instead of asking for a password or API key in chat. The user "
            "fills a secure card and you receive the account name plus placeholders. "
            "A returned placeholder is a ready-to-use credential: pass it as the "
            "argument value of the tool that needs it, in the same turn."
        )

    async def execute(
        self,
        connector_id: str = "",
        method: str | None = None,
        purpose: str = "",
        allowed_tools: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        timeout_seconds: int = 300,
        **kwargs: object,
    ) -> str:
        _ = kwargs
        manager = get_credential_manager()
        if not manager.auth.has_listener:
            return ToolResult.error(
                "Error: no interactive client is attached, so the user cannot complete "
                "an authentication card. Ask the user to connect the account in Settings."
            )
        try:
            connector = manager.connector(connector_id.strip())
        except KeyError:
            available = ", ".join(item["id"] for item in manager.connectors())
            return ToolResult.error(
                f"Error: unknown connector '{connector_id}'. Available: {available}"
            )
        try:
            auth_method = (
                AuthMethod(method) if method else connector.auth_methods[0]
            )
        except ValueError:
            options = ", ".join(item.value for item in connector.auth_methods)
            return ToolResult.error(f"Error: unknown method '{method}'. Available: {options}")
        if auth_method not in connector.auth_methods:
            return ToolResult.error(
                f"Error: {connector.id} does not support {auth_method.value}"
            )

        try:
            request = manager.request_auth(
                connector_id=connector.id,
                method=auth_method,
                purpose=purpose.strip()[:400],
                run_id=_run_id(),
                allowed_tools=allowed_tools or (),
                allowed_domains=allowed_domains or (),
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

        wait_seconds = max(30, min(_MAX_WAIT_SECONDS, int(timeout_seconds or 300)))
        try:
            completed = await manager.auth.wait(request.id, timeout=wait_seconds)
        except asyncio.CancelledError:
            manager.cancel_auth_request(request.id)
            raise
        if completed.status is AuthRequestStatus.COMPLETED and completed.account_id:
            account = manager.account(completed.account_id)
            payload = {
                "status": "connected",
                "account_id": completed.account_id,
                "connector_id": connector.id,
                "label": account.label if account else connector.name,
                "placeholders": manager.placeholders_for_account(completed.account_id),
                "next_step": _PLACEHOLDER_HINT,
            }
            return json.dumps(payload, ensure_ascii=False)
        if completed.status is AuthRequestStatus.CANCELLED:
            return ToolResult.error("Error: the user cancelled the authentication request")
        if completed.status is AuthRequestStatus.EXPIRED:
            return ToolResult.error("Error: the authentication request expired")
        return ToolResult.error(
            f"Error: authentication did not complete: {completed.status.value}"
        )


@tool_parameters(
    tool_parameters_schema(
        required=[],
    )
)
class ListConnectionsTool(Tool):
    """Report connectors and connected accounts without exposing secrets."""

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:  # noqa: ARG003
        return cls()

    @property
    def name(self) -> str:
        return "list_connections"

    @property
    def description(self) -> str:
        return (
            "List connectors and accounts the user has already connected. "
            "Use the returned connector and account identifiers to choose a tool; "
            "a connected account's placeholders are ready to pass to that tool."
        )

    async def execute(self, **kwargs: object) -> str:
        _ = kwargs
        manager = get_credential_manager()
        accounts: list[dict[str, Any]] = []
        for account in manager.accounts():
            accounts.append({
                **account,
                "placeholders": manager.placeholders_for_account(account["id"]),
            })
        payload: dict[str, Any] = {
            "connectors": manager.connectors(),
            "accounts": accounts,
        }
        if any(account["placeholders"] for account in accounts):
            payload["next_step"] = _PLACEHOLDER_HINT
        return json.dumps(payload, ensure_ascii=False)
