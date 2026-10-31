"""Authorization-code + PKCE flow for connector OAuth2 accounts.

Yoreni is the OAuth client. The browser is redirected to the provider, the
provider redirects back to the gateway callback, and the gateway exchanges the
code. The access token is written straight into the encrypted credential store
and bound to an account; it never reaches the model, the chat transcript, or the
browser.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from yoreni.credentials.manager import CredentialManager
from yoreni.credentials.models import Account, ConnectorSpec
from yoreni.security.network import validate_url_target

CONNECTOR_OAUTH_CALLBACK_PATH = "/auth/connectors/callback"
_FLOW_TTL_S = 600


class OAuthFlowError(ValueError):
    """A safe, user-facing OAuth failure."""


@dataclass
class PendingOAuthFlow:
    flow_id: str
    state: str
    code_verifier: str
    connector_id: str
    auth_request_id: str
    redirect_uri: str
    run_id: str | None
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + _FLOW_TTL_S)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def validate_connector_redirect_uri(redirect_uri: str) -> str:
    cleaned = redirect_uri.strip()
    parsed = urlsplit(cleaned)
    if (
        not parsed.netloc
        or not parsed.hostname
        or parsed.path != CONNECTOR_OAUTH_CALLBACK_PATH
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OAuthFlowError("Invalid connector OAuth callback URL")
    if parsed.scheme == "https":
        return cleaned
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
        return cleaned
    raise OAuthFlowError("Connector OAuth callbacks must use HTTPS or localhost")


class ConnectorOAuthFlowManager:
    """Issues authorization URLs and completes the callback exchange."""

    def __init__(self, manager: CredentialManager) -> None:
        self._manager = manager
        self._flows: dict[str, PendingOAuthFlow] = {}

    @property
    def manager(self) -> CredentialManager:
        return self._manager

    def start(
        self,
        connector_id: str,
        *,
        auth_request_id: str,
        redirect_uri: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        connector = self._manager.connector(connector_id)
        if not connector.oauth_authorization_url or not connector.oauth_token_url:
            raise OAuthFlowError("This connector has no OAuth2 endpoints configured")
        redirect = validate_connector_redirect_uri(redirect_uri)
        flow = PendingOAuthFlow(
            flow_id=f"oauth_{secrets.token_hex(8)}",
            state=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(64),
            connector_id=connector_id,
            auth_request_id=auth_request_id,
            redirect_uri=redirect,
            run_id=run_id,
        )
        self._expire()
        self._flows[flow.state] = flow
        params = {
            "response_type": "code",
            "client_id": connector.oauth_client_id,
            "redirect_uri": redirect,
            "state": flow.state,
            "code_challenge": _pkce_challenge(flow.code_verifier),
            "code_challenge_method": "S256",
        }
        if connector.oauth_scopes:
            params["scope"] = " ".join(connector.oauth_scopes)
        separator = "&" if urlsplit(connector.oauth_authorization_url).query else "?"
        return {
            "flow_id": flow.flow_id,
            "authorization_url": connector.oauth_authorization_url + separator + urlencode(params),
            "expires_at": flow.expires_at,
        }

    async def handle_callback(self, state: str, code: str) -> Account:
        self._expire()
        flow = self._flows.pop(state, None)
        if flow is None:
            raise OAuthFlowError("Authorization state is unknown or expired")
        connector = self._manager.connector(flow.connector_id)
        token_url = connector.oauth_token_url
        try:
            validate_url_target(token_url)
        except Exception as exc:
            raise OAuthFlowError("OAuth token endpoint is not allowed") from exc
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow.redirect_uri,
            "client_id": connector.oauth_client_id,
            "code_verifier": flow.code_verifier,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                token_url,
                data=payload,
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise OAuthFlowError(f"Token exchange rejected ({response.status_code})")
        tokens = _token_payload(response)
        expires_at = float(tokens.pop("_expires_at")) if "_expires_at" in tokens else None
        return self._manager.complete_oauth(
            flow.auth_request_id,
            tokens,
            expires_at=expires_at,
        )

    def cancel(self, state: str) -> None:
        self._flows.pop(state, None)

    def _expire(self) -> None:
        now = time.time()
        for state, flow in list(self._flows.items()):
            if flow.expires_at <= now:
                self._flows.pop(state, None)


def _token_payload(response: httpx.Response) -> dict[str, str]:
    try:
        loaded = response.json()
    except ValueError as exc:
        raise OAuthFlowError("Token endpoint returned a non-JSON response") from exc
    if not isinstance(loaded, dict):
        raise OAuthFlowError("Token endpoint returned an invalid response")
    body = cast(dict[object, object], loaded)
    tokens: dict[str, str] = {}
    for key in ("access_token", "refresh_token", "id_token", "token_type", "scope"):
        value = body.get(key)
        if isinstance(value, str) and value:
            tokens[key] = value
    expires_in = body.get("expires_in")
    if isinstance(expires_in, (int, float)):
        tokens["_expires_at"] = str(time.time() + float(expires_in))
    return tokens


async def refresh_account(manager: CredentialManager, account_id: str) -> bool:
    """Refresh an expired OAuth account; returns whether new tokens were stored."""
    stored = manager.store.get_account(account_id)
    if stored is None:
        return False
    refresh_token = stored.payload.get("refresh_token")
    if not refresh_token:
        return False
    connector: ConnectorSpec = manager.connector(stored.account.connector_id)
    if not connector.oauth_token_url:
        return False
    try:
        validate_url_target(connector.oauth_token_url)
    except Exception:
        return False
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            connector.oauth_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": connector.oauth_client_id,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        manager.mark_expired(account_id)
        return False
    tokens = _token_payload(response)
    tokens.setdefault("refresh_token", refresh_token)
    expires_at = float(tokens.pop("_expires_at")) if "_expires_at" in tokens else None
    manager.update_oauth_tokens(account_id, tokens, expires_at=expires_at)
    return True


def parse_callback_query(raw_query: str) -> tuple[str, str]:
    """Extract and validate ``state``/``code`` from a callback query string."""
    params = parse_qs(raw_query, keep_blank_values=False)
    state = (params.get("state") or [""])[0]
    code = (params.get("code") or [""])[0]
    error = (params.get("error") or [""])[0]
    if error:
        raise OAuthFlowError(f"Authorization was denied: {error}")
    if not state or not code:
        raise OAuthFlowError("The callback did not include state and code")
    return state, code
