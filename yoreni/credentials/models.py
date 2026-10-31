"""Connector, account, and authentication vocabulary.

A connector describes *how* one external service authenticates. An account is
one user's concrete connection through that connector. A credential is one
sensitive field owned by an account. None of these records exposes a secret;
secrets live only inside the encrypted payload of an account.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AuthMethod(str, Enum):
    """Supported authentication shapes for a connector."""

    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"
    CUSTOM = "custom"
    NONE = "none"
    ONE_TIME_CODE = "one_time_code"


class CredentialKind(str, Enum):
    """How long one credential field may live."""

    PERSISTENT = "persistent"
    SHORT_LIVED = "short_lived"
    ONE_TIME = "one_time"


class AccountStatus(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ConnectorField:
    """One authentication input a connector requires."""

    name: str
    label: str
    kind: CredentialKind = CredentialKind.PERSISTENT
    secret: bool = True
    required: bool = True
    placeholder: str = ""

    def __post_init__(self) -> None:
        if not _FIELD_RE.match(self.name):
            raise ValueError(f"invalid connector field name: {self.name!r}")


@dataclass(frozen=True)
class ConnectorSpec:
    """A connector definition: authentication plus what it can be used for."""

    id: str
    name: str
    description: str = ""
    # Presentation only: a stable icon key and accent colour for Settings.
    brand: str = ""
    color: str = ""
    auth_methods: tuple[AuthMethod, ...] = (AuthMethod.API_KEY,)
    fields: tuple[ConnectorField, ...] = ()
    scopes: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    # Tools this connector's accounts may restore credentials into by default.
    tool_patterns: tuple[str, ...] = ()
    oauth_authorization_url: str = ""
    oauth_token_url: str = ""
    oauth_client_id: str = ""
    oauth_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(f"invalid connector id: {self.id!r}")
        if not self.name.strip():
            raise ValueError("connector name is required")

    def fields_for(self, method: AuthMethod) -> tuple[ConnectorField, ...]:
        if method is AuthMethod.OAUTH2:
            return ()
        return self.fields

    def to_public_dict(self) -> dict[str, Any]:
        """Describe the connector without any secret material."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "brand": self.brand,
            "color": self.color,
            "auth_methods": [method.value for method in self.auth_methods],
            "oauth_ready": bool(
                self.oauth_authorization_url
                and self.oauth_token_url
                and self.oauth_client_id
            ),
            "fields": [
                {
                    "name": field.name,
                    "label": field.label,
                    "kind": field.kind.value,
                    "secret": field.secret,
                    "required": field.required,
                    "placeholder": field.placeholder,
                }
                for field in self.fields
            ],
            "scopes": list(self.scopes),
            "allowed_domains": list(self.allowed_domains),
            "tool_patterns": list(self.tool_patterns),
        }


@dataclass
class Account:
    """One user's connection through a connector.

    ``secret_fields`` holds field names only. Values are readable exclusively
    through :meth:`yoreni.credentials.manager.CredentialManager.reveal`, which is
    restricted to the tool-restoration boundary.
    """

    id: str
    connector_id: str
    label: str
    auth_method: AuthMethod
    status: AccountStatus = AccountStatus.CONNECTED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    restorable: bool = False
    secret_fields: tuple[str, ...] = ()

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "connector_id": self.connector_id,
            "label": self.label,
            "auth_method": self.auth_method.value,
            "status": (
                AccountStatus.EXPIRED.value
                if self.status is AccountStatus.CONNECTED and self.expired
                else self.status.value
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "allowed_tools": list(self.allowed_tools),
            "allowed_domains": list(self.allowed_domains),
            "restorable": self.restorable,
            "secret_fields": list(self.secret_fields),
        }


def validate_connector_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError("connector id must be a lowercase slug")
    return value


def validate_field_name(value: str) -> str:
    if not _FIELD_RE.match(value):
        raise ValueError(f"invalid credential field name: {value!r}")
    return value
