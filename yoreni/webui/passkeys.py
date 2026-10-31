"""Passkey credentials for WebUI sign-in.

Only the owner registers credentials, and only for this instance, so the store
is one JSON document beside the config that keeps the stable user handle and
one record per authenticator. Ceremony challenges live in memory because they
expire within minutes and must never outlive the process that issued them.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yoreni.config.paths import get_data_dir
from yoreni.profile.store import PASSKEY_FILENAME
from yoreni.utils.file_io import write_text_atomic
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.webauthn import (
    WebAuthnError,
    assertion_challenge,
    authentication_options,
    b64url_decode,
    b64url_encode,
    new_challenge,
    registration_options,
    verify_assertion,
    verify_registration,
)

CHALLENGE_TTL_S = 120
CEREMONY_TIMEOUT_MS = 60_000
LOGIN_TICKET_TTL_S = 30 * 24 * 60 * 60
_MAX_CHALLENGES = 256
_MAX_AUTHENTICATORS = 32
_DOCUMENT_VERSION = 1
_NAME_MAX = 64


class PasskeyError(ValueError):
    """A ceremony request the caller must correct."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class Authenticator(BaseModel):
    """One registered credential, bound to the origin that created it."""

    model_config = ConfigDict(extra="ignore")

    id: str
    credential_id: str
    rp_id: str
    name: str = ""
    public_key: str
    algorithm: int
    sign_count: int = 0
    aaguid: str = ""
    transports: list[str] = Field(default_factory=list)
    created_at: str
    last_used_at: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or "This device",
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    def allowed_credential(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "type": "public-key",
            "id": self.credential_id,
        }
        if self.transports:
            entry["transports"] = list(self.transports)
        return entry


class PasskeyDocument(BaseModel):
    """The durable passkey file: one user handle, many credentials."""

    model_config = ConfigDict(extra="ignore")

    version: int = _DOCUMENT_VERSION
    user_handle: str
    authenticators: list[Authenticator] = Field(default_factory=list)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise PasskeyError(
            "The authenticator file is unreadable; restore or remove it", status=500
        ) from None
    if not isinstance(value, dict):
        raise PasskeyError(
            "The authenticator file is unreadable; restore or remove it", status=500
        )
    return cast(dict[str, Any], value)


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    with suppress(OSError):
        path.chmod(0o600)


class PasskeyStore:
    """Read and replace the passkey document under one lock."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_data_dir() / PASSKEY_FILENAME
        self._lock = threading.RLock()

    def document(self) -> PasskeyDocument:
        with self._lock:
            return self._load()

    def add(self, authenticator: Authenticator) -> Authenticator:
        with self._lock:
            document = self._load()
            if len(document.authenticators) >= _MAX_AUTHENTICATORS:
                raise PasskeyError(
                    f"You can register at most {_MAX_AUTHENTICATORS} authenticators",
                    status=409,
                )
            if any(
                item.credential_id == authenticator.credential_id
                for item in document.authenticators
            ):
                raise PasskeyError("This authenticator is already registered", status=409)
            document.authenticators.append(authenticator)
            self._save(document)
            return authenticator

    def remove(self, authenticator_id: str) -> None:
        with self._lock:
            document = self._load()
            remaining = [
                item for item in document.authenticators if item.id != authenticator_id
            ]
            if len(remaining) == len(document.authenticators):
                raise PasskeyError("Authenticator not found", status=404)
            document.authenticators = remaining
            self._save(document)

    def record_use(self, credential_id: str, sign_count: int) -> None:
        with self._lock:
            document = self._load()
            for item in document.authenticators:
                if item.credential_id == credential_id:
                    item.sign_count = sign_count
                    item.last_used_at = _now_iso()
                    self._save(document)
                    return

    def _load(self) -> PasskeyDocument:
        data = _read_object(self.path)
        if data is not None:
            try:
                return PasskeyDocument.model_validate(data)
            except ValidationError:
                raise PasskeyError(
                    "The authenticator file is unreadable; restore or remove it",
                    status=500,
                ) from None
        document = PasskeyDocument(user_handle=secrets.token_hex(16))
        try:
            self._save(document)
        except OSError:
            # A read-only data directory must not block the WebUI password path;
            # registration will fail later with the same write error.
            return document
        return document

    def _save(self, document: PasskeyDocument) -> None:
        _write_private(
            self.path,
            {"version": _DOCUMENT_VERSION, **document.model_dump(mode="json")},
        )


@dataclass
class _Challenge:
    kind: Literal["register", "login"]
    rp_id: str
    origin: str
    expires_at: float
    name: str = ""


def origin_rp_id(origin: str) -> str:
    """Return the relying party ID a browser is allowed to use for this origin."""
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"}:
        raise PasskeyError("Authenticators need an http or https origin", status=409)
    host = parts.hostname or ""
    if not host:
        raise PasskeyError("Authenticators need a host name", status=409)
    if _is_ip_literal(host):
        raise PasskeyError(
            "Authenticators need a domain name or localhost, not an IP address",
            status=409,
        )
    return host


def _is_ip_literal(host: str) -> bool:
    if ":" in host:
        return True
    parts = host.split(".")
    return len(parts) == 4 and all(part.isdigit() for part in parts)


def _websocket_config(config: Any) -> Any:
    from yoreni.channels.websocket.runtime import WebSocketConfig

    current: object = getattr(config.channels, "websocket", None) or {}
    return WebSocketConfig.model_validate(current)


class PasskeyService:
    """Issue and verify WebAuthn ceremonies for the owner of this instance."""

    def __init__(
        self,
        config: Any,
        *,
        tokens: GatewayTokenStore,
        store: PasskeyStore | None = None,
        ticket_ttl_s: int = LOGIN_TICKET_TTL_S,
    ) -> None:
        self._config = config
        self._tokens = tokens
        self._store = store or PasskeyStore()
        self._ticket_ttl_s = ticket_ttl_s
        self._challenges: dict[str, _Challenge] = {}
        self._lock = threading.Lock()

    # -- reads ---------------------------------------------------------------

    def password_set(self) -> bool:
        websocket = _websocket_config(self._config.load())
        return bool(websocket.token_issue_secret.strip() or websocket.token.strip())

    def status(self, origin: str | None) -> dict[str, Any]:
        return {
            "password_set": self.password_set(),
            "passkey_count": len(self._for_origin(origin)),
        }

    def list(self, origin: str | None) -> dict[str, Any]:
        return {
            "password_set": self.password_set(),
            "authenticators": [item.summary() for item in self._for_origin(origin)],
        }

    # -- registration --------------------------------------------------------

    def registration_options(self, origin: str | None, name: Any = None) -> dict[str, Any]:
        request_origin = _require_origin(origin)
        rp_id = origin_rp_id(request_origin)
        document = self._store.document()
        challenge = new_challenge()
        label = _authenticator_name(name)
        self._remember_challenge(
            challenge,
            _Challenge(
                kind="register",
                rp_id=rp_id,
                origin=request_origin,
                expires_at=time.monotonic() + CHALLENGE_TTL_S,
                name=label,
            ),
        )
        options = registration_options(
            challenge=challenge,
            rp_id=rp_id,
            rp_name="Yoreni",
            user_id=bytes.fromhex(document.user_handle),
            user_name="yoreni",
            display_name="Yoreni",
            exclude=[
                item.allowed_credential()
                for item in document.authenticators
                if item.rp_id == rp_id
            ],
            timeout_ms=CEREMONY_TIMEOUT_MS,
        )
        return options

    def register(self, payload: Any, origin: str | None) -> dict[str, Any]:
        data = _payload(payload)
        request_origin = _require_origin(origin)
        raw_client_data = _decode_field(_field(data, "client_data_json"), "client data")
        challenge = _challenge_from_client_data(raw_client_data)
        record = self._take_challenge(challenge, "register", request_origin)
        rp_id = record.rp_id
        try:
            registered = verify_registration(
                raw_client_data,
                _decode_field(_field(data, "attestation_object"), "attestation"),
                challenge=challenge,
                rp_id=rp_id,
                origin=request_origin,
            )
        except WebAuthnError as exc:
            raise PasskeyError(f"Registration was rejected: {exc}") from None

        name = _authenticator_name(data.get("name")) or record.name
        authenticator = Authenticator(
            id=secrets.token_hex(8),
            credential_id=b64url_encode(registered.credential_id),
            rp_id=rp_id,
            name=name,
            public_key=b64url_encode(registered.public_key),
            algorithm=registered.algorithm,
            sign_count=registered.sign_count,
            aaguid=registered.aaguid.hex(),
            transports=_transports(data.get("transports")),
            created_at=_now_iso(),
        )
        self._store.add(authenticator)
        return authenticator.summary()

    # -- authentication ------------------------------------------------------

    def authentication_options(self, origin: str | None) -> dict[str, Any]:
        request_origin = _require_origin(origin)
        rp_id = origin_rp_id(request_origin)
        credentials = self._store.document().authenticators
        allowed = [
            item.allowed_credential() for item in credentials if item.rp_id == rp_id
        ]
        if not allowed:
            raise PasskeyError(
                "No authenticator is registered for this address", status=404
            )
        challenge = new_challenge()
        self._remember_challenge(
            challenge,
            _Challenge(
                kind="login",
                rp_id=rp_id,
                origin=request_origin,
                expires_at=time.monotonic() + CHALLENGE_TTL_S,
            ),
        )
        options = authentication_options(
            challenge=challenge,
            rp_id=rp_id,
            allow=allowed,
            timeout_ms=CEREMONY_TIMEOUT_MS,
        )
        return options

    def authenticate(self, payload: Any, origin: str | None) -> dict[str, Any]:
        data = _payload(payload)
        request_origin = _require_origin(origin)
        raw_client_data = _decode_field(_field(data, "client_data_json"), "client data")
        challenge = _challenge_from_client_data(raw_client_data)
        record = self._take_challenge(challenge, "login", request_origin)

        credential_id = _field(data, "credential_id")
        authenticator = next(
            (
                item
                for item in self._store.document().authenticators
                if item.credential_id == credential_id and item.rp_id == record.rp_id
            ),
            None,
        )
        if authenticator is None:
            raise PasskeyError("This authenticator is not registered here", status=404)
        try:
            sign_count = verify_assertion(
                raw_client_data,
                _decode_field(_field(data, "authenticator_data"), "authenticator data"),
                _decode_field(_field(data, "signature"), "signature"),
                challenge=challenge,
                rp_id=record.rp_id,
                origin=request_origin,
                credential_public_key=b64url_decode(authenticator.public_key),
                stored_sign_count=authenticator.sign_count,
            )
        except WebAuthnError as exc:
            raise PasskeyError(f"Sign-in was rejected: {exc}", status=401) from None

        self._store.record_use(authenticator.credential_id, sign_count)
        return {
            "ticket": self._tokens.issue_login_ticket(self._ticket_ttl_s),
            "expires_in": self._ticket_ttl_s,
        }

    def remove(self, origin: str | None, authenticator_id: Any) -> dict[str, Any]:
        if not isinstance(authenticator_id, str) or not authenticator_id:
            raise PasskeyError("Choose an authenticator to remove")
        self._store.remove(authenticator_id)
        return self.list(origin)

    # -- internals -----------------------------------------------------------

    def _for_origin(self, origin: str | None) -> list[Authenticator]:
        if not origin:
            return []
        try:
            rp_id = origin_rp_id(origin)
        except PasskeyError:
            return []
        return [
            item
            for item in self._store.document().authenticators
            if item.rp_id == rp_id
        ]

    def _remember_challenge(self, challenge: str, record: _Challenge) -> None:
        now = time.monotonic()
        with self._lock:
            for key, value in list(self._challenges.items()):
                if value.expires_at <= now:
                    self._challenges.pop(key, None)
            if len(self._challenges) >= _MAX_CHALLENGES:
                oldest = min(self._challenges, key=lambda key: self._challenges[key].expires_at)
                self._challenges.pop(oldest, None)
            self._challenges[challenge] = record

    def _take_challenge(
        self, challenge: str, kind: Literal["register", "login"], origin: str
    ) -> _Challenge:
        with self._lock:
            record = self._challenges.pop(challenge, None)
        if record is None or record.kind != kind or record.expires_at <= time.monotonic():
            raise PasskeyError("This sign-in attempt expired; try again")
        if record.origin != origin:
            raise PasskeyError("This sign-in attempt was issued for another address")
        return record


def _require_origin(origin: str | None) -> str:
    if not origin:
        raise PasskeyError("Authenticators require a browser Origin", status=400)
    return origin


def _payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PasskeyError("The authenticator response is malformed")
    return cast(dict[str, Any], payload)


def _field(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PasskeyError("The authenticator response is incomplete")
    return value


def _decode_field(value: str, label: str) -> bytes:
    try:
        return b64url_decode(value)
    except WebAuthnError:
        raise PasskeyError(f"The {label} is malformed") from None


def _challenge_from_client_data(client_data_json: bytes) -> str:
    try:
        challenge = assertion_challenge(client_data_json)
    except WebAuthnError:
        raise PasskeyError("The client data is malformed") from None
    if challenge is None:
        raise PasskeyError("The authenticator response is incomplete")
    return challenge


def _authenticator_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:_NAME_MAX]


def _transports(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    allowed = {"ble", "cable", "hybrid", "internal", "nfc", "smart-card", "usb"}
    entries = cast(list[object], value)
    return [
        item
        for item in entries
        if isinstance(item, str) and item in allowed
    ][:8]


__all__ = [
    "Authenticator",
    "PasskeyDocument",
    "PasskeyError",
    "PasskeyService",
    "PasskeyStore",
    "origin_rp_id",
]
