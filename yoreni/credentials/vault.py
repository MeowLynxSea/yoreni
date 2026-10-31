"""Deterministic secret placeholders and two-way redaction.

Placeholders are derived with HMAC over the secret value, so the same secret
always maps to the same token within one instance. That keeps multi-round and
streamed context stable without ever putting the real value in a prompt.

Redaction is intentionally conservative: when a secret that belongs to this
vault is found it is replaced, and if any known secret survives the rewrite the
caller receives :class:`RedactionError` instead of a payload to send.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from dataclasses import dataclass, field, replace
from typing import Any, cast

from yoreni.credentials.models import CredentialKind

PLACEHOLDER_RE = re.compile(r"\{\{YORENI_SECRET_[0-9a-f]{16}\}\}")
_UNKNOWN_PLACEHOLDER_PREFIX = "{{YORENI_SECRET_REDACTED_"
_MIN_SECRET_LEN = 4

# High-confidence shapes for secrets the vault has never seen. These are
# redacted without a mapping, so they can never be restored into a tool call.
_UNKNOWN_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*\b"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]{32,}"
        r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
)


class RedactionError(RuntimeError):
    """Raised when a payload cannot be proven free of protected secrets."""


@dataclass(frozen=True)
class SecretEntry:
    secret: str
    account_id: str
    field: str
    restorable: bool
    allowed_tools: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    kind: CredentialKind = CredentialKind.PERSISTENT


@dataclass
class _VaultState:
    tokens: dict[str, SecretEntry] = field(default_factory=dict)
    by_secret: dict[str, str] = field(default_factory=dict)
    external: set[str] = field(default_factory=set)


def _token_digest(secret: str, key: bytes) -> str:
    return hmac.new(key, secret.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _unknown_token(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
    return f"{_UNKNOWN_PLACEHOLDER_PREFIX}{digest}}}"


class SecretVault:
    """Instance-wide mapping between secret values and stable placeholders."""

    def __init__(self, key: bytes) -> None:
        self._key = key
        self._lock = threading.RLock()
        self._state = _VaultState()

    # -- registration -----------------------------------------------------

    def token_for(self, secret: str) -> str:
        return "{{YORENI_SECRET_" + _token_digest(secret, self._key) + "}}"

    def register(self, entry: SecretEntry) -> str | None:
        """Register a tool-restorable credential; returns its placeholder."""
        if len(entry.secret) < _MIN_SECRET_LEN:
            return None
        token = self.token_for(entry.secret)
        with self._lock:
            self._state.tokens[token] = entry
            self._state.by_secret[entry.secret] = token
        return token

    def register_external(self, secret: str | None) -> None:
        """Register a secret that must be redacted but never restored."""
        if not secret or len(secret) < _MIN_SECRET_LEN:
            return
        with self._lock:
            self._state.external.add(secret)
            self._state.by_secret.setdefault(secret, self.token_for(secret))

    def forget_account(self, account_id: str) -> None:
        with self._lock:
            tokens = [
                token for token, entry in self._state.tokens.items()
                if entry.account_id == account_id
            ]
            for token in tokens:
                entry = self._state.tokens.pop(token)
                self._state.by_secret.pop(entry.secret, None)

    def suppress_account(self, account_id: str) -> None:
        """Keep redacting an account's secrets but forbid further restoration.

        Revocation, expiry, and one-time consumption remove the permission to
        hand a secret to a tool. The value can still appear in a tool echo, so
        its placeholder mapping must survive.
        """
        with self._lock:
            for token, entry in list(self._state.tokens.items()):
                if entry.account_id == account_id:
                    self._state.tokens[token] = replace(entry, restorable=False)

    def clear(self) -> None:
        with self._lock:
            self._state = _VaultState()

    def all_secrets(self) -> list[str]:
        with self._lock:
            return sorted(
                set(self._state.external) | {entry.secret for entry in self._state.tokens.values()},
                key=len,
                reverse=True,
            )

    # -- lookup -----------------------------------------------------------

    def entry_for_token(self, token: str) -> SecretEntry | None:
        with self._lock:
            return self._state.tokens.get(token)

    def entries(self) -> list[SecretEntry]:
        """Return registered credential entries for policy evaluation."""
        with self._lock:
            return list(self._state.tokens.values())

    def placeholders_in(self, text: str) -> list[str]:
        return PLACEHOLDER_RE.findall(text)

    def is_unknown_placeholder(self, text: str) -> bool:
        return text.startswith(_UNKNOWN_PLACEHOLDER_PREFIX)

    # -- redaction --------------------------------------------------------

    def _replace_known(self, text: str) -> str:
        secrets = self.all_secrets()
        for secret in secrets:
            if secret in text:
                token = self._state.by_secret.get(secret) or self.token_for(secret)
                text = text.replace(secret, token)
        return text

    def _replace_unknown_patterns(self, text: str) -> str:
        for pattern in _UNKNOWN_SECRET_PATTERNS:
            text = pattern.sub(lambda match: _unknown_token(match.group(0)), text)
        return text

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        redacted = self._replace_known(text)
        redacted = self._replace_unknown_patterns(redacted)
        return redacted

    def redact_value(self, value: object) -> object:
        """Recursively redact strings in a JSON-shaped value."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.redact_value(item) for item in cast(list[object], value)]
        if isinstance(value, tuple):
            return tuple(self.redact_value(item) for item in cast(tuple[object, ...], value))
        if isinstance(value, dict):
            mapping = cast(dict[object, object], value)
            return {key: self.redact_value(item) for key, item in mapping.items()}
        return value

    def assert_clean(self, value: object) -> None:
        """Fail closed when a protected secret survived redaction."""
        serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        for secret in self.all_secrets():
            if secret in serialized:
                raise RedactionError("protected secret survived outbound redaction")

    def redact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Redact an entire provider message list before it leaves the process."""
        redacted = cast(list[dict[str, Any]], self.redact_value(messages))
        self.assert_clean(redacted)
        return redacted


_vault_instance: SecretVault | None = None
_vault_guard = threading.Lock()


def get_vault() -> SecretVault:
    """Return the process-wide vault, loading the instance key lazily."""
    global _vault_instance
    with _vault_guard:
        if _vault_instance is None:
            from yoreni.credentials.crypto import (
                encode_master_key,
                load_or_create_master_key,
                redaction_key,
            )

            master = load_or_create_master_key()
            instance = SecretVault(redaction_key(master))
            # The key file itself must never survive redaction if some tool
            # prints it.
            instance.register_external(encode_master_key(master).strip())
            instance.register_external(master.hex())
            _vault_instance = instance
        return _vault_instance


def reset_vault(vault: SecretVault | None = None) -> None:
    global _vault_instance
    with _vault_guard:
        _vault_instance = vault
