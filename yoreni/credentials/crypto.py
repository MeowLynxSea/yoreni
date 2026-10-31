"""Master-key handling and authenticated encryption for stored credentials.

The master key is never written into the agent workspace. It lives beside the
instance configuration under ``keys/`` with owner-only permissions, or is
supplied by ``YORENI_CREDENTIAL_KEY`` for managed deployments. Ciphertext is
bound to its record identifier with AES-GCM associated data so a stored value
cannot be moved to another account.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from yoreni.config.paths import get_data_dir

KEY_ENV = "YORENI_CREDENTIAL_KEY"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_VERSION = "v1"


class CredentialCryptoError(RuntimeError):
    """Raised when the master key or a stored ciphertext cannot be used."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def master_key_path() -> Path:
    return get_data_dir() / "keys" / "credentials.key"


def _decode_master_key(raw: str, *, source: str) -> bytes:
    try:
        key = _b64decode(raw.strip())
    except (binascii.Error, ValueError) as exc:
        raise CredentialCryptoError(f"{source} is not valid base64") from exc
    if len(key) != _KEY_BYTES:
        raise CredentialCryptoError(f"{source} must decode to {_KEY_BYTES} bytes")
    return key


def load_master_key() -> bytes | None:
    """Return the configured master key, or ``None`` when none exists yet."""
    env_value = os.environ.get(KEY_ENV, "").strip()
    if env_value:
        return _decode_master_key(env_value, source=KEY_ENV)
    path = master_key_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CredentialCryptoError(f"cannot read {path.name}: {exc}") from exc
    return _decode_master_key(raw, source=str(path))


def load_or_create_master_key() -> bytes:
    """Load the instance master key, creating one atomically on first use."""
    existing = load_master_key()
    if existing is not None:
        return existing
    if os.environ.get(KEY_ENV, "").strip():
        raise CredentialCryptoError(f"{KEY_ENV} is set but unusable")

    path = master_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    key = secrets.token_bytes(_KEY_BYTES)
    encoded = (_b64encode(key) + "\n").encode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_or_create_master_key()
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise CredentialCryptoError(f"cannot write {path.name}: {exc}") from exc
    return key


def redaction_key(master_key: bytes) -> bytes:
    """Derive the key used for deterministic secret-placeholder identifiers."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"yoreni.credential.redaction",
        info=b"secret-token-v1",
    ).derive(master_key)


def new_master_key() -> bytes:
    """Generate a fresh 32-byte master key."""
    return secrets.token_bytes(_KEY_BYTES)


def encode_master_key(key: bytes) -> str:
    """Encode a master key for the on-disk key file."""
    if len(key) != _KEY_BYTES:
        raise CredentialCryptoError("master key must be 32 bytes")
    return _b64encode(key) + "\n"


class SecretBox:
    """AES-GCM wrapper that binds ciphertext to one account identifier."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise CredentialCryptoError("secret box key must be 32 bytes")
        self._aead = AESGCM(key)

    def seal(self, plaintext: str, *, aad: str) -> str:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return f"{_VERSION}.{_b64encode(nonce)}.{_b64encode(ciphertext)}"

    def open(self, token: str, *, aad: str) -> str:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != _VERSION:
            raise CredentialCryptoError("unsupported credential ciphertext format")
        try:
            nonce = _b64decode(parts[1])
            ciphertext = _b64decode(parts[2])
            plaintext = self._aead.decrypt(nonce, ciphertext, aad.encode("utf-8"))
        except Exception as exc:
            raise CredentialCryptoError("credential ciphertext failed authentication") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialCryptoError("credential payload is not UTF-8") from exc
