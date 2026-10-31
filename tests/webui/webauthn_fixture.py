"""A synthetic authenticator for WebAuthn ceremony tests.

Browsers produce the payloads this module builds; keeping the construction
here avoids a real browser in tests while exercising the same byte layout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL = 0x40


def cbor_encode(value: Any) -> bytes:
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        return (
            _cbor_head(0, value) if value >= 0 else _cbor_head(1, -1 - value)
        )
    if isinstance(value, bytes):
        return _cbor_head(2, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _cbor_head(3, len(encoded)) + encoded
    if isinstance(value, list):
        return _cbor_head(4, len(value)) + b"".join(cbor_encode(item) for item in value)
    if isinstance(value, dict):
        return _cbor_head(5, len(value)) + b"".join(
            cbor_encode(key) + cbor_encode(item) for key, item in value.items()
        )
    raise TypeError(f"unsupported CBOR value: {type(value)!r}")


def _cbor_head(major: int, value: int) -> bytes:
    if value < 24:
        return bytes([major << 5 | value])
    if value < 0x100:
        return bytes([major << 5 | 24, value])
    if value < 0x10000:
        return bytes([major << 5 | 25]) + value.to_bytes(2, "big")
    if value < 0x100000000:
        return bytes([major << 5 | 26]) + value.to_bytes(4, "big")
    return bytes([major << 5 | 27]) + value.to_bytes(8, "big")


class FakeAuthenticator:
    """An ES256 credential that signs ceremonies for one relying party."""

    def __init__(self, rp_id: str) -> None:
        self.rp_id = rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.aaguid = bytes.fromhex("00112233445566778899aabbccddeeff")
        self.sign_count = 0
        numbers = self.key.public_key().public_numbers()
        self.cose = cbor_encode({
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def client_data(self, challenge: str, origin: str, kind: str) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def auth_data(
        self,
        *,
        flags: int,
        sign_count: int,
        attested: bool = True,
    ) -> bytes:
        data = (
            hashlib.sha256(self.rp_id.encode("utf-8")).digest()
            + bytes([flags])
            + sign_count.to_bytes(4, "big")
        )
        if not attested:
            return data
        return (
            data
            + self.aaguid
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self.cose
        )

    def attestation(
        self,
        challenge: str,
        origin: str,
        *,
        flags: int = FLAG_USER_PRESENT | FLAG_USER_VERIFIED | FLAG_ATTESTED_CREDENTIAL,
        sign_count: int | None = None,
    ) -> tuple[str, str]:
        count = self.sign_count if sign_count is None else sign_count
        client_data = self.client_data(challenge, origin, "webauthn.create")
        attestation_object = cbor_encode({
            "fmt": "none",
            "attStmt": {},
            "authData": self.auth_data(flags=flags, sign_count=count),
        })
        return _b64(client_data), _b64(attestation_object)

    def assertion(
        self,
        challenge: str,
        origin: str,
        *,
        flags: int = FLAG_USER_PRESENT,
        sign_count: int | None = None,
    ) -> tuple[str, str, str, str]:
        count = self.sign_count if sign_count is None else sign_count
        client_data = self.client_data(challenge, origin, "webauthn.get")
        auth_data = self.auth_data(flags=flags, sign_count=count, attested=False)
        digest = hashlib.sha256(client_data).digest()
        signature = self.key.sign(auth_data + digest, ec.ECDSA(hashes.SHA256()))
        return (
            _b64(client_data),
            _b64(auth_data),
            _b64(signature),
            _b64(self.credential_id),
        )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
