"""WebUI-facing connector and credential operations.

Every function here returns metadata only. Secret values enter through
``connect_account`` and ``submit_auth_request`` and are sealed before the call
returns; no response echoes them back.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from yoreni.credentials.manager import get_credential_manager
from yoreni.credentials.models import (
    AuthMethod,
    ConnectorField,
    ConnectorSpec,
    CredentialKind,
    validate_connector_id,
)
from yoreni.credentials.oauth import ConnectorOAuthFlowManager

_oauth_flows: ConnectorOAuthFlowManager | None = None


def _oauth() -> ConnectorOAuthFlowManager:
    global _oauth_flows
    manager = get_credential_manager()
    if _oauth_flows is None or _oauth_flows.manager is not manager:
        _oauth_flows = ConnectorOAuthFlowManager(manager)
    return _oauth_flows


def credentials_state() -> dict[str, Any]:
    manager = get_credential_manager()
    return {
        "connectors": manager.connectors(),
        "accounts": manager.accounts(),
        "pending": [request.public_dict() for request in manager.auth.pending()],
    }


def configure_oauth(payload: Mapping[str, object]) -> dict[str, Any]:
    connector_id = _required_str(payload, "connector_id")
    client_id = _required_str(payload, "client_id")
    return {"connector": get_credential_manager().set_oauth_client_id(connector_id, client_id)}


def connect_account(payload: Mapping[str, object]) -> dict[str, Any]:
    manager = get_credential_manager()
    connector_id = _required_str(payload, "connector_id")
    spec = manager.connector(connector_id)
    raw_method = payload.get("method")
    method = (
        AuthMethod(raw_method)
        if isinstance(raw_method, str) and raw_method
        else spec.auth_methods[0]
    )
    account = manager.connect(
        connector_id,
        label=_optional_str(payload, "label") or "",
        method=method,
        values=_values(payload),
        allowed_tools=_str_list(payload.get("allowed_tools")),
        allowed_domains=_str_list(payload.get("allowed_domains")),
        restorable=bool(payload.get("restorable", True)),
    )
    return {"account": account.to_public_dict()}


def update_account(payload: Mapping[str, object]) -> dict[str, Any]:
    account_id = _required_str(payload, "account_id")
    raw_restorable = payload.get("restorable")
    account = get_credential_manager().update_account(
        account_id,
        label=_optional_str(payload, "label"),
        allowed_tools=_str_list(payload["allowed_tools"]) if "allowed_tools" in payload else None,
        allowed_domains=_str_list(payload["allowed_domains"]) if "allowed_domains" in payload else None,
        restorable=raw_restorable if isinstance(raw_restorable, bool) else None,
    )
    return {"account": account.to_public_dict()}


def delete_account(payload: Mapping[str, object]) -> dict[str, Any]:
    account_id = _required_str(payload, "account_id")
    if not get_credential_manager().disconnect(account_id):
        raise KeyError(account_id)
    return {"deleted": True}


def revoke_account(payload: Mapping[str, object]) -> dict[str, Any]:
    account_id = _required_str(payload, "account_id")
    if get_credential_manager().account(account_id) is None:
        raise KeyError(account_id)
    get_credential_manager().revoke(account_id)
    return {"revoked": True}


def submit_auth_request(payload: Mapping[str, object]) -> dict[str, Any]:
    request_id = _required_str(payload, "request_id")
    account = get_credential_manager().submit_auth_request(
        request_id,
        _values(payload),
        label=_optional_str(payload, "label"),
    )
    return {"account": account.to_public_dict()}


def cancel_auth_request(payload: Mapping[str, object]) -> dict[str, Any]:
    request_id = _required_str(payload, "request_id")
    request = get_credential_manager().cancel_auth_request(request_id)
    if request is None:
        raise KeyError(request_id)
    return {"request": request.public_dict()}


def start_oauth(payload: Mapping[str, object]) -> dict[str, Any]:
    return _oauth().start(
        _required_str(payload, "connector_id"),
        auth_request_id=_required_str(payload, "request_id"),
        redirect_uri=_required_str(payload, "redirect_uri"),
        run_id=_optional_str(payload, "run_id"),
    )


async def complete_oauth_callback(state: str, code: str) -> dict[str, Any]:
    account = await _oauth().handle_callback(state, code)
    return {"account": account.to_public_dict()}


def register_connector(payload: Mapping[str, object]) -> dict[str, Any]:
    connector_id = validate_connector_id(_required_str(payload, "id"))
    raw_fields = payload.get("fields")
    if raw_fields is None:
        raw_fields = []
    if not isinstance(raw_fields, list):
        raise ValueError("fields must be a list")
    fields: list[ConnectorField] = []
    for raw_item in cast(list[object], raw_fields):
        if not isinstance(raw_item, dict):
            raise ValueError("each field must be an object")
        item = cast(dict[object, object], raw_item)
        fields.append(ConnectorField(
            name=str(item.get("name") or ""),
            label=str(item.get("label") or item.get("name") or ""),
            kind=CredentialKind(str(item.get("kind") or "persistent")),
            secret=bool(item.get("secret", True)),
            required=bool(item.get("required", True)),
        ))
    raw_methods = payload.get("auth_methods") or ["api_key"]
    if not isinstance(raw_methods, list):
        raise ValueError("auth_methods must be a list")
    spec = ConnectorSpec(
        id=connector_id,
        name=_required_str(payload, "name"),
        description=str(payload.get("description") or ""),
        auth_methods=tuple(AuthMethod(str(method)) for method in cast(list[object], raw_methods)),
        fields=tuple(fields),
        scopes=tuple(_str_list(payload.get("scopes"))),
        allowed_domains=tuple(_str_list(payload.get("allowed_domains"))),
        tool_patterns=tuple(_str_list(payload.get("tool_patterns"))),
        oauth_authorization_url=str(payload.get("oauth_authorization_url") or ""),
        oauth_token_url=str(payload.get("oauth_token_url") or ""),
        oauth_client_id=str(payload.get("oauth_client_id") or ""),
        oauth_scopes=tuple(_str_list(payload.get("oauth_scopes"))),
    )
    return {"connector": get_credential_manager().register_connector(spec)}


def rotate_master_key() -> dict[str, Any]:
    count = get_credential_manager().rotate_master_key()
    return {"rewrapped": count}


def _values(payload: Mapping[str, object]) -> dict[str, str]:
    raw = payload.get("values")
    if not isinstance(raw, dict):
        raise ValueError("values must be an object")
    return {
        str(key): str(value)
        for key, value in cast(dict[object, object], raw).items()
    }


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{key} is required")


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    return [str(item) for item in cast(list[object], value) if isinstance(item, str) and item.strip()]
