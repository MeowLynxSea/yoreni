"""The single entry point for connector, account, and credential management.

Everything that needs a secret — provider redaction, tool restoration, WebUI
account management — goes through :class:`CredentialManager`. The manager keeps
the vault in sync with encrypted storage, enforces field contracts, and is the
only component that can turn a placeholder back into a value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from yoreni.credentials.auth import AuthRequest, AuthRequestManager, AuthRequestStatus
from yoreni.credentials.crypto import (
    CredentialCryptoError,
    SecretBox,
    master_key_path,
    redaction_key,
)
from yoreni.credentials.models import (
    Account,
    AccountStatus,
    AuthMethod,
    ConnectorField,
    ConnectorSpec,
    CredentialKind,
    CredentialUseDeniedError,
    validate_connector_id,
)
from yoreni.credentials.store import (
    CredentialStore,
    new_account_id,
    new_auth_request_id,
)
from yoreni.credentials.vault import SecretEntry, SecretVault, get_vault

_log = logging.getLogger(__name__)

# Credential-shaped fields. OAuth material and one-time codes are never
# restorable into a tool call.
_RESTORABLE_FIELDS = frozenset({
    "username",
    "user",
    "email",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "token",
    "secret",
    "client_secret",
    "code",
    "otp",
})

# OAuth material is stored but never handed to an arbitrary tool.
_NON_RESTORABLE_FIELDS = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "expires_at",
    "scope",
    "token_type",
})


def builtin_connectors() -> tuple[ConnectorSpec, ...]:
    """Connectors that need no registration because their shape is generic."""
    return (
        ConnectorSpec(
            id="api_key",
            name="API key service",
            description="A service authenticated with a single API key or bearer token.",
            auth_methods=(AuthMethod.API_KEY, AuthMethod.BEARER),
            fields=(
                ConnectorField("api_key", "API key", secret=True),
            ),
            scopes=("read", "write"),
        ),
        ConnectorSpec(
            id="basic_auth",
            name="Username and password",
            description="A service authenticated with an account and password.",
            auth_methods=(AuthMethod.BASIC,),
            fields=(
                ConnectorField("username", "Username", kind=CredentialKind.PERSISTENT),
                ConnectorField("password", "Password", secret=True),
            ),
        ),
        ConnectorSpec(
            id="one_time_code",
            name="One-time code",
            description="A verification code that is used once and never stored.",
            auth_methods=(AuthMethod.ONE_TIME_CODE,),
            fields=(
                ConnectorField("code", "Verification code", kind=CredentialKind.ONE_TIME),
            ),
        ),
        ConnectorSpec(
            id="public_service",
            name="No authentication",
            description="A public endpoint that requires no credential.",
            auth_methods=(AuthMethod.NONE,),
        ),
        ConnectorSpec(
            id="mcp_server",
            name="MCP server",
            description="A remote MCP server authorized through its OAuth flow.",
            auth_methods=(AuthMethod.OAUTH2, AuthMethod.API_KEY),
            fields=(
                ConnectorField("api_key", "API key", secret=True, required=False),
            ),
        ),
    )


class CredentialManager:
    """Owns connectors, accounts, the vault, and pending authentication."""

    def __init__(
        self,
        *,
        store: CredentialStore | None = None,
        vault: SecretVault | None = None,
        notify_auth: Callable[[AuthRequest], None] | None = None,
        auth_ttl_seconds: float = 600,
    ) -> None:
        self.vault = vault or get_vault()
        self.store = store or CredentialStore()
        self.auth = AuthRequestManager(ttl_seconds=auth_ttl_seconds, notify=notify_auth)
        self._connectors: dict[str, ConnectorSpec] = {
            spec.id: spec for spec in builtin_connectors()
        }
        self._lock = threading.RLock()
        self._loaded = False
        self._external_secrets: set[str] = set()
        # One-time codes live only in memory and disappear after first use.
        self._one_time: dict[str, dict[str, str]] = {}
        self._ephemeral: dict[str, Account] = {}

    # -- lifecycle --------------------------------------------------------

    def load(self) -> None:
        """Load persisted connectors/accounts into memory and the vault."""
        with self._lock:
            if self._loaded:
                return
            for raw in self.store.list_connectors():
                try:
                    spec = _connector_from_dict(raw)
                except (ValueError, TypeError):
                    _log.warning("ignoring malformed persisted connector %r", raw.get("id"))
                    continue
                self._connectors[spec.id] = spec
            stored_accounts = self.store.list_accounts()
            for stored in stored_accounts:
                self._register_account(stored.account, stored.payload)
            self._loaded = True
        # A pending request cannot survive a process restart: the blocked Run
        # is gone. Mark them expired instead of leaving a phantom card.
        self.store.expire_auth_requests()
        self.migrate_legacy_mcp_tokens()

    def set_auth_listener(self, listener: Callable[[AuthRequest], None] | None) -> None:
        """Install the sink that turns requests into WebUI cards."""
        self.auth.set_notify(listener)

    def reload(self) -> None:
        with self._lock:
            self.vault.clear()
            self._loaded = False
        self.load()

    # -- connectors -------------------------------------------------------

    def connectors(self) -> list[dict[str, Any]]:
        self.load()
        with self._lock:
            return [spec.to_public_dict() for spec in sorted(self._connectors.values(), key=lambda s: s.id)]

    def connector(self, connector_id: str) -> ConnectorSpec:
        self.load()
        with self._lock:
            spec = self._connectors.get(connector_id)
        if spec is None:
            raise KeyError(connector_id)
        return spec

    def register_connector(self, spec: ConnectorSpec) -> dict[str, Any]:
        self.store.save_connector(spec)
        with self._lock:
            self._connectors[spec.id] = spec
        return spec.to_public_dict()

    def set_oauth_client_id(self, connector_id: str, client_id: str) -> dict[str, Any]:
        """Store the operator's public OAuth client id for a connector."""
        spec = self.connector(connector_id)
        updated = replace(spec, oauth_client_id=client_id.strip())
        return self.register_connector(updated)

    # -- accounts ---------------------------------------------------------

    def accounts(self) -> list[dict[str, Any]]:
        self.load()
        self._expire_ephemeral()
        public = [stored.account.to_public_dict() for stored in self.store.list_accounts()]
        public.extend(account.to_public_dict() for account in self._ephemeral.values())
        return public

    def account(self, account_id: str) -> Account | None:
        self.load()
        self._expire_ephemeral()
        stored = self.store.get_account(account_id)
        if stored is not None:
            return stored.account
        return self._ephemeral.get(account_id)

    def _expire_ephemeral(self) -> None:
        now = time.time()
        with self._lock:
            for account_id, account in list(self._ephemeral.items()):
                if account.expires_at is not None and account.expires_at <= now:
                    self._ephemeral.pop(account_id, None)
                    self._one_time.pop(account_id, None)
                    self.vault.suppress_account(account_id)

    def connect(
        self,
        connector_id: str,
        *,
        label: str,
        method: AuthMethod,
        values: Mapping[str, str],
        restorable: bool = True,
        expires_at: float | None = None,
        account_id: str | None = None,
        persist: bool = True,
    ) -> Account:
        """Create or update one account from user-supplied values.

        ``persist=False`` keeps the account in memory only: it is usable for
        the rest of the process and disappears on restart.
        """
        self.load()
        spec = self.connector(connector_id)
        if method not in spec.auth_methods:
            raise ValueError(f"{connector_id} does not support {method.value} authentication")
        payload = self._validated_payload(spec, method, values)
        now = time.time()
        account = Account(
            id=account_id or new_account_id(),
            connector_id=connector_id,
            label=label.strip() or spec.name,
            auth_method=method,
            status=AccountStatus.CONNECTED,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            restorable=restorable,
            ephemeral=not persist,
            secret_fields=tuple(sorted(payload)),
        )
        one_time_fields = {
            field.name for field in spec.fields_for(method)
            if field.kind is CredentialKind.ONE_TIME
        }
        transient = {name: value for name, value in payload.items() if name in one_time_fields}
        persistent = {name: value for name, value in payload.items() if name not in one_time_fields}
        if persist and persistent:
            self.store.save_account(account, persistent)
        with self._lock:
            if transient:
                account.expires_at = expires_at or now + 300
                self._one_time[account.id] = transient
            # Nothing to persist means nothing to restore after a restart; such
            # accounts are session-only by construction.
            if not persist or not persistent:
                account.ephemeral = True
                self._ephemeral[account.id] = account
            self._register_account(account, payload)
        return account

    def update_account(
        self,
        account_id: str,
        *,
        label: str | None = None,
        restorable: bool | None = None,
    ) -> Account:
        stored = self.store.get_account(account_id)
        if stored is None:
            raise KeyError(account_id)
        account = stored.account
        self.vault.suppress_account(account_id)
        if label is not None:
            account.label = label.strip() or account.label
        if restorable is not None:
            account.restorable = restorable
        account.updated_at = time.time()
        self.store.save_account(account, stored.payload)
        with self._lock:
            self._register_account(account, stored.payload)
        return account

    def disconnect(self, account_id: str) -> bool:
        """Revoke an account and drop its placeholders immediately."""
        with self._lock:
            self.vault.suppress_account(account_id)
            self._one_time.pop(account_id, None)
            ephemeral = self._ephemeral.pop(account_id, None)
            removed = self.store.delete_account(account_id)
            return removed or ephemeral is not None

    def mark_expired(self, account_id: str) -> None:
        self.vault.suppress_account(account_id)
        self.store.set_account_status(account_id, AccountStatus.EXPIRED)

    def revoke(self, account_id: str) -> bool:
        self.vault.suppress_account(account_id)
        self.store.set_account_status(account_id, AccountStatus.REVOKED)
        return True

    def reveal(self, account_id: str, field: str) -> str | None:
        """Return one decrypted field. Callers must be trusted internal code."""
        stored = self.store.get_account(account_id)
        if stored is None:
            return None
        return stored.payload.get(field)

    # -- authentication requests -----------------------------------------

    def request_auth(
        self,
        *,
        connector_id: str,
        method: AuthMethod,
        purpose: str,
        run_id: str | None,
        ttl_seconds: float | None = None,
        label: str = "",
        ephemeral: bool = False,
    ) -> AuthRequest:
        self.load()
        spec = self.connector(connector_id)
        if method not in spec.auth_methods:
            raise ValueError(f"{connector_id} does not support {method.value} authentication")
        if ephemeral and method is AuthMethod.OAUTH2:
            raise ValueError("OAuth accounts are always saved; ephemeral is not supported")
        request = self.auth.create(
            new_auth_request_id(),
            connector_id=connector_id,
            method=method,
            fields=tuple(field.name for field in spec.fields_for(method)),
            purpose=purpose,
            run_id=run_id,
            ttl_seconds=ttl_seconds,
            label=label.strip()[:80],
            ephemeral=ephemeral,
        )
        self.store.save_auth_request(request.public_dict())
        return request

    def submit_auth_request(
        self,
        request_id: str,
        values: Mapping[str, str],
        *,
        label: str | None = None,
    ) -> Account:
        """Persist values submitted through the protected authentication channel."""
        request = self.auth.get(request_id)
        if request is None:
            raise KeyError(request_id)
        lock = self.auth.lock(request_id)
        with lock:
            if request.status is not AuthRequestStatus.PENDING:
                raise ValueError("authentication request is no longer pending")
            if request.expired:
                self.auth.cancel(request_id)
                raise ValueError("authentication request expired")
            spec = self.connector(request.connector_id)
            account = self.connect(
                request.connector_id,
                label=label or request.label or spec.name,
                method=request.method,
                values=values,
                restorable=True,
                persist=not request.ephemeral,
            )
            self.auth.complete(request_id, account.id)
            self.store.set_auth_request_status(request_id, AuthRequestStatus.COMPLETED.value)
            return account

    def complete_oauth(
        self,
        request_id: str,
        tokens: Mapping[str, str],
        *,
        expires_at: float | None = None,
        label: str | None = None,
    ) -> Account:
        """Finish an OAuth request after the callback validated state and PKCE."""
        request = self.auth.get(request_id)
        if request is None:
            raise KeyError(request_id)
        lock = self.auth.lock(request_id)
        with lock:
            if request.status is not AuthRequestStatus.PENDING:
                raise ValueError("authentication request is no longer pending")
            payload = {
                str(key): str(value)
                for key, value in tokens.items()
                if value
            }
            if not payload.get("access_token"):
                raise ValueError("OAuth response did not contain an access token")
            if expires_at is not None:
                payload["expires_at"] = str(expires_at)
            spec = self.connector(request.connector_id)
            now = time.time()
            account = Account(
                id=new_account_id(),
                connector_id=request.connector_id,
                label=label or request.label or spec.name,
                auth_method=AuthMethod.OAUTH2,
                status=AccountStatus.CONNECTED,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
                restorable=False,
                secret_fields=tuple(sorted(payload)),
            )
            self.store.save_account(account, payload)
            with self._lock:
                self._register_account(account, payload)
            self.auth.complete(request_id, account.id)
            self.store.set_auth_request_status(request_id, AuthRequestStatus.COMPLETED.value)
            return account

    def fail_auth_request(self, request_id: str, message: str) -> None:
        self.auth.fail(request_id, message)
        self.store.set_auth_request_status(request_id, AuthRequestStatus.FAILED.value)

    def update_oauth_tokens(
        self,
        account_id: str,
        tokens: Mapping[str, str],
        *,
        expires_at: float | None = None,
    ) -> Account:
        """Replace an OAuth account's tokens after a refresh."""
        stored = self.store.get_account(account_id)
        if stored is None:
            raise KeyError(account_id)
        account = stored.account
        payload = dict(stored.payload)
        for key, value in tokens.items():
            if value:
                payload[str(key)] = value
        if expires_at is not None:
            payload["expires_at"] = str(expires_at)
            account.expires_at = expires_at
        account.updated_at = time.time()
        account.secret_fields = tuple(sorted(payload))
        self.store.save_account(account, payload)
        self.vault.suppress_account(account_id)
        with self._lock:
            self._register_account(account, payload)
        return account

    def cancel_auth_request(self, request_id: str) -> AuthRequest | None:
        request = self.auth.cancel(request_id)
        if request is not None:
            self.store.set_auth_request_status(request_id, request.status.value)
        return request

    # -- redaction and restoration ---------------------------------------

    def register_external_secret(self, secret: str | None, *, label: str = "") -> None:
        """Register a secret that is redacted but never restored into tools."""
        if not secret or len(secret) < 4:
            return
        with self._lock:
            if secret in self._external_secrets:
                return
            self._external_secrets.add(secret)
        self.vault.register_external(secret)
        _ = label

    def redact(self, text: str) -> str:
        return self.vault.redact_text(text)

    def redact_value(self, value: Any) -> Any:
        return self.vault.redact_value(value)

    def redact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.vault.redact_messages(messages)

    def restore_arguments(self, params: dict[str, Any]) -> dict[str, Any]:
        """Replace credential placeholders in a tool call with their secrets."""
        self._assert_accounts_usable(params)
        restored = cast(dict[str, Any], self.vault.restore_value(params))
        for value in _walk_strings(params):
            for token in self.vault.placeholders_in(value):
                entry = self.vault.entry_for_token(token)
                if entry is not None and entry.field in self._one_time.get(entry.account_id, {}):
                    self._consume_one_time(entry.account_id)
        return restored

    def _assert_accounts_usable(self, params: dict[str, Any]) -> None:
        """Reject a call whose credential belongs to a revoked or expired account."""
        for value in _walk_strings(params):
            for token in self.vault.placeholders_in(value):
                entry = self.vault.entry_for_token(token)
                if entry is None:
                    continue
                account = self.account(entry.account_id)
                if account is None:
                    raise CredentialUseDeniedError(
                        f"account {entry.account_id} no longer exists"
                    )
                if account.status is not AccountStatus.CONNECTED or account.expired:
                    raise CredentialUseDeniedError(
                        f"account {entry.account_id} is no longer usable"
                    )

    def placeholders_for_account(self, account_id: str) -> dict[str, str]:
        """Return only the placeholder references a model may reuse.

        The returned values are opaque tokens, never secrets. They are limited
        to fields the account may restore into a tool call.
        """
        account = self.account(account_id)
        if account is None or account.status is not AccountStatus.CONNECTED or account.expired:
            return {}
        return {
            entry.field: self.vault.token_for(entry.secret)
            for entry in self.vault.entries()
            if entry.account_id == account_id and entry.restorable
        }

    def _consume_one_time(self, account_id: str) -> None:
        """Invalidate a one-time credential after its first successful use."""
        with self._lock:
            self._one_time.pop(account_id, None)
            self._ephemeral.pop(account_id, None)
            self.vault.suppress_account(account_id)
            self.store.delete_account(account_id)

    # -- key rotation -----------------------------------------------------

    def rotate_master_key(self) -> int:
        """Re-encrypt every account under a fresh master key.

        The new key is staged before any payload changes, and the previous key
        is kept beside it until the swap succeeds. A rewrap failure leaves the
        database and the active key untouched; an operator can recover from the
        backup if the final rename cannot complete.
        """
        if os.environ.get("YORENI_CREDENTIAL_KEY", "").strip():
            raise CredentialCryptoError(
                "cannot rotate an externally supplied master key"
            )
        from yoreni.credentials.crypto import encode_master_key, load_master_key, new_master_key

        new_key = new_master_key()
        path = master_key_path()
        staged = path.with_suffix(path.suffix + ".new")
        backup = path.with_suffix(path.suffix + ".previous")
        staged.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encode_master_key(new_key))
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

        previous = load_master_key()
        if previous is not None:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encode_master_key(previous))
                handle.flush()
                os.fsync(handle.fileno())

        try:
            count = self.store.rewrap(SecretBox(new_key))
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        os.replace(staged, path)
        from yoreni.credentials.vault import reset_vault

        vault = SecretVault(redaction_key(new_key))
        # The old vault owned redaction for provider keys and other external
        # secrets. Move that registration, or the new vault would silently stop
        # protecting values whose dedupe entry still exists.
        for secret in self._external_secrets:
            vault.register_external(secret)
        vault.register_external(encode_master_key(new_key).strip())
        vault.register_external(new_key.hex())
        reset_vault(vault)
        self.vault = vault
        # Session-only accounts and one-time codes have no ciphertext to rewrap.
        # Dropping them keeps the listed accounts consistent with what the new
        # vault can actually restore.
        with self._lock:
            self._ephemeral.clear()
            self._one_time.clear()
        for stored in self.store.list_accounts():
            self._register_account(stored.account, stored.payload)
        return count

    # -- legacy migration -------------------------------------------------

    def migrate_legacy_mcp_tokens(self) -> int:
        """Import legacy MCP OAuth tokens for redaction without deleting them.

        The MCP client keeps owning its token file; this copy exists so the
        tokens can never appear in a model prompt. A failed read is reported and
        changes nothing, which keeps the original credentials usable.
        """
        path = _mcp_token_path()
        if path is None or not path.exists():
            return 0
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("could not read legacy MCP token store for redaction import")
            return 0
        if not isinstance(loaded, dict):
            return 0
        servers = cast(dict[object, object], loaded).get("servers")
        if not isinstance(servers, dict):
            return 0
        existing = {account.account.id for account in self.store.list_accounts()}
        imported = 0
        servers_map = cast(dict[object, object], servers)
        for name, entry in servers_map.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            entry_map = cast(dict[object, object], entry)
            tokens = entry_map.get("tokens")
            if not isinstance(tokens, dict):
                continue
            tokens_map = cast(dict[object, object], tokens)
            values: dict[str, str] = {}
            for key in ("access_token", "refresh_token"):
                value = tokens_map.get(key)
                if isinstance(value, str) and value:
                    values[key] = value
                    self.register_external_secret(value, label=f"mcp:{name}")
            if not values:
                continue
            fingerprint = str(entry_map.get("server_fingerprint") or "")
            digest = hashlib.sha256(f"{name}:{fingerprint}".encode("utf-8")).hexdigest()[:16]
            account_id = f"mcp_{digest}"
            if account_id in existing:
                continue
            if "expires_at" in entry_map:
                values["expires_at"] = str(entry_map["expires_at"])
            account = Account(
                id=account_id,
                connector_id="mcp_server",
                label=f"MCP: {name}",
                auth_method=AuthMethod.OAUTH2,
                restorable=False,
                secret_fields=tuple(sorted(values)),
            )
            self.store.save_account(account, values)
            imported += 1
        return imported

    # -- internals --------------------------------------------------------

    def _validated_payload(
        self,
        spec: ConnectorSpec,
        method: AuthMethod,
        values: Mapping[str, str],
    ) -> dict[str, str]:
        fields = spec.fields_for(method)
        if method is AuthMethod.OAUTH2:
            raise ValueError("OAuth2 accounts are created by the authorization flow")
        payload: dict[str, str] = {}
        for field in fields:
            value = values.get(field.name)
            if value is None or not str(value):
                if field.required:
                    raise ValueError(f"missing required field: {field.name}")
                continue
            payload[field.name] = str(value)
        for name in values:
            if name not in {field.name for field in fields}:
                raise ValueError(f"unexpected field: {name}")
        return payload

    def _register_account(self, account: Account, payload: Mapping[str, str]) -> None:
        spec = self._connectors.get(account.connector_id)
        kinds = {
            field.name: field.kind for field in spec.fields_for(account.auth_method)
        } if spec is not None else {}
        for field_name, value in payload.items():
            restorable = (
                account.restorable
                and account.status is AccountStatus.CONNECTED
                and not account.expired
                and field_name in _RESTORABLE_FIELDS
                and field_name not in _NON_RESTORABLE_FIELDS
            )
            self.vault.register(SecretEntry(
                secret=str(value),
                account_id=account.id,
                field=field_name,
                restorable=restorable,
                kind=kinds.get(field_name, CredentialKind.PERSISTENT),
            ))


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    found: list[str] = []
    if isinstance(value, dict):
        for item in cast(dict[object, object], value).values():
            found.extend(_walk_strings(item))
    elif isinstance(value, (list, tuple)):
        for item in cast(list[object] | tuple[object, ...], value):
            found.extend(_walk_strings(item))
    return found


def _connector_from_dict(raw: dict[str, Any]) -> ConnectorSpec:
    data = cast(dict[object, object], raw)
    raw_fields = data.get("fields")
    fields: list[ConnectorField] = []
    if isinstance(raw_fields, list):
        for item in cast(list[object], raw_fields):
            if not isinstance(item, dict) or "name" not in item:
                continue
            field_data = cast(dict[object, object], item)
            fields.append(ConnectorField(
                name=str(field_data["name"]),
                label=str(field_data.get("label") or field_data["name"]),
                kind=CredentialKind(str(field_data.get("kind") or "persistent")),
                secret=bool(field_data.get("secret", True)),
                required=bool(field_data.get("required", True)),
                placeholder=str(field_data.get("placeholder") or ""),
            ))
    auth_methods = data.get("auth_methods")
    scopes = data.get("scopes")
    oauth_scopes = data.get("oauth_scopes")

    def _strings(value: object, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
        if not isinstance(value, list):
            return fallback
        return tuple(str(item) for item in cast(list[object], value))

    return ConnectorSpec(
        id=validate_connector_id(str(data["id"])),
        name=str(data.get("name") or data["id"]),
        description=str(data.get("description") or ""),
        brand=str(data.get("brand") or ""),
        color=str(data.get("color") or ""),
        auth_methods=tuple(
            AuthMethod(str(method)) for method in _strings(auth_methods, ("api_key",))
        ),
        fields=tuple(fields),
        scopes=_strings(scopes),
        oauth_authorization_url=str(data.get("oauth_authorization_url") or ""),
        oauth_token_url=str(data.get("oauth_token_url") or ""),
        oauth_client_id=str(data.get("oauth_client_id") or ""),
        oauth_scopes=_strings(oauth_scopes),
    )


def _mcp_token_path() -> Path | None:
    try:
        from yoreni.config.paths import get_data_dir

        return get_data_dir() / "auth" / "mcp.json"
    except Exception:
        return None


_manager_instance: CredentialManager | None = None
_manager_guard = threading.Lock()


def get_credential_manager() -> CredentialManager:
    """Return the process-wide credential manager."""
    global _manager_instance
    with _manager_guard:
        if _manager_instance is None:
            _manager_instance = CredentialManager()
            _manager_instance.load()
        return _manager_instance


def reset_credential_manager(manager: CredentialManager | None = None) -> None:
    global _manager_instance
    with _manager_guard:
        _manager_instance = manager
