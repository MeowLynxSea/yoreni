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
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)
from yoreni.credentials.auth import AuthRequestStatus
from yoreni.credentials.manager import get_credential_manager
from yoreni.credentials.models import AuthMethod

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
            "One sentence shown to the user explaining which service is being connected "
            "and why. Never promise how the values are kept: the card states that."
        ),
        label=StringSchema(
            "Name for this account in the user's account list, e.g. 'Campus portal' or "
            "'Work email'. Defaults to the connector's name."
        ),
        ephemeral=BooleanSchema(
            description=(
                "True when the account is only needed for this task: it stays in memory "
                "for the session and is never saved. Defaults to false."
            ),
            default=False,
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
            "Give the account a short label and set ephemeral when it is only needed "
            "for this task. A returned placeholder is a ready-to-use credential: pass "
            "it as the argument value of the tool that needs it, in the same turn."
        )

    async def execute(
        self,
        connector_id: str = "",
        method: str | None = None,
        purpose: str = "",
        label: str = "",
        ephemeral: bool = False,
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

        # The card is the user's authorization: once they fill it, the account is
        # usable by whatever tool the model calls next. Nothing is inferred from
        # the current page, and no tool name has to be guessed up front.
        try:
            request = manager.request_auth(
                connector_id=connector.id,
                method=auth_method,
                purpose=purpose.strip()[:400],
                run_id=_run_id(),
                label=label,
                ephemeral=ephemeral,
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

        # Wait for as long as the Run lives. The user may need to open a browser,
        # sign in, and approve a second factor; stopping the card mid-way would
        # throw away that work.
        try:
            completed = await manager.auth.wait(request.id)
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
                "ephemeral": account.ephemeral if account else ephemeral,
                "placeholders": manager.placeholders_for_account(completed.account_id),
                "next_step": _PLACEHOLDER_HINT,
            }
            return json.dumps(payload, ensure_ascii=False)
        if completed.status is AuthRequestStatus.CANCELLED:
            return ToolResult.error("Error: the user cancelled the authentication request")
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
        for account in manager.accounts(include_ephemeral=True):
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
