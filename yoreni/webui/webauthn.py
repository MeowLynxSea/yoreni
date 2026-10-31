"""WebAuthn verification primitives for the WebUI gateway.

The gateway verifies ceremonies produced by browsers talking to this instance,
so only the subset the WebUI needs is implemented: registration with an
unverified attestation statement and assertion verification for stored
credentials. CBOR decoding is limited to the definite-length items a browser
sends, which keeps the module free of a third-party dependency.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, TypeAlias, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

__all__ = [
    "AuthData",
    "RegisteredCredential",
    "WebAuthnError",
    "assertion_challenge",
    "authentication_options",
    "b64url_decode",
    "b64url_encode",
    "decode_cbor",
    "new_challenge",
    "public_key_from_cose",
    "registration_options",
    "verify_assertion",
    "verify_registration",
]

CHALLENGE_BYTES = 32
CLIENT_DATA_MAX_BYTES = 4 * 1024
_OBJECT_MAX_BYTES = 64 * 1024
_CBOR_MAX_DEPTH = 8
_CREDENTIAL_ID_MAX_BYTES = 1023
# Authenticator data flags (WebAuthn Level 2, section 6.1).
_FLAG_USER_PRESENT = 0x01
_FLAG_USER_VERIFIED = 0x04
_FLAG_ATTESTED_CREDENTIAL = 0x40
_FLAG_EXTENSION_DATA = 0x80
_ES256 = -7
_RS256 = -257
_EDDSA = -8


class WebAuthnError(ValueError):
    """A ceremony payload that must be rejected."""


# The decoded shape of a CBOR item; callers narrow each field they use.
CBORValue: TypeAlias = (
    int | bool | bytes | str | None | list["CBORValue"] | dict[int | str, "CBORValue"]
)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not text:
        raise WebAuthnError("empty base64url value")
    body = text.rstrip("=")
    if not body or any(character not in _B64URL_ALPHABET for character in body):
        raise WebAuthnError("invalid base64url value")
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        raise WebAuthnError("invalid base64url value") from None


_B64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def new_challenge() -> str:
    return b64url_encode(secrets.token_bytes(CHALLENGE_BYTES))


def assertion_challenge(client_data_json: bytes) -> str | None:
    """Return the echoed challenge from client data without verifying it."""
    data = _client_data(client_data_json)
    challenge = data.get("challenge")
    return challenge if isinstance(challenge, str) else None


# -- CBOR ------------------------------------------------------------------


def decode_cbor(data: bytes) -> CBORValue:
    """Decode one definite-length CBOR item and reject trailing bytes."""
    value, consumed = _decode_cbor_item(data, 0, 0)
    if consumed != len(data):
        raise WebAuthnError("trailing bytes after CBOR value")
    return value


def _decode_cbor_prefix(data: bytes) -> tuple[CBORValue, int]:
    return _decode_cbor_item(data, 0, 0)


def _decode_cbor_item(data: bytes, offset: int, depth: int) -> tuple[CBORValue, int]:
    if depth > _CBOR_MAX_DEPTH:
        raise WebAuthnError("CBOR value is nested too deeply")
    if offset >= len(data):
        raise WebAuthnError("truncated CBOR value")
    initial = data[offset]
    major = initial >> 5
    additional = initial & 0x1F
    offset += 1

    if major == 7:
        if additional == 20:
            return False, offset
        if additional == 21:
            return True, offset
        if additional == 22:
            return None, offset
        raise WebAuthnError("unsupported CBOR simple value")
    if major == 6:
        raise WebAuthnError("CBOR tags are not supported")

    length, offset = _decode_cbor_length(data, offset, additional)
    if major == 0:
        return length, offset
    if major == 1:
        return -1 - length, offset
    if major in (2, 3):
        if length > _OBJECT_MAX_BYTES:
            raise WebAuthnError("CBOR value is too large")
        end = offset + length
        if end > len(data):
            raise WebAuthnError("truncated CBOR value")
        raw = data[offset:end]
        if major == 2:
            return raw, end
        try:
            return raw.decode("utf-8"), end
        except UnicodeDecodeError:
            raise WebAuthnError("CBOR text is not valid UTF-8") from None
    if major == 4:
        if length > _OBJECT_MAX_BYTES:
            raise WebAuthnError("CBOR value is too large")
        items: list[CBORValue] = []
        for _ in range(length):
            item, offset = _decode_cbor_item(data, offset, depth + 1)
            items.append(item)
        return items, offset
    if major == 5:
        if length > _OBJECT_MAX_BYTES:
            raise WebAuthnError("CBOR value is too large")
        mapping: dict[int | str, CBORValue] = {}
        for _ in range(length):
            key, offset = _decode_cbor_item(data, offset, depth + 1)
            if isinstance(key, bool) or not isinstance(key, (int, str)):
                raise WebAuthnError("unsupported CBOR map key")
            if key in mapping:
                raise WebAuthnError("duplicate CBOR map key")
            value, offset = _decode_cbor_item(data, offset, depth + 1)
            mapping[key] = value
        return mapping, offset
    raise WebAuthnError("unsupported CBOR major type")


def _decode_cbor_length(data: bytes, offset: int, additional: int) -> tuple[int, int]:
    if additional < 24:
        return additional, offset
    size = {24: 1, 25: 2, 26: 4, 27: 8}.get(additional)
    if size is None:
        raise WebAuthnError("indefinite CBOR lengths are not supported")
    if offset + size > len(data):
        raise WebAuthnError("truncated CBOR value")
    return int.from_bytes(data[offset : offset + size], "big"), offset + size


# -- Authenticator data ----------------------------------------------------


@dataclass(frozen=True)
class AuthData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    aaguid: bytes | None = None
    credential_id: bytes | None = None
    credential_public_key: bytes | None = None

    @property
    def user_present(self) -> bool:
        return bool(self.flags & _FLAG_USER_PRESENT)

    @property
    def user_verified(self) -> bool:
        return bool(self.flags & _FLAG_USER_VERIFIED)


def parse_auth_data(raw: bytes) -> AuthData:
    if len(raw) < 37:
        raise WebAuthnError("authenticator data is truncated")
    rp_id_hash = raw[0:32]
    flags = raw[32]
    sign_count = int.from_bytes(raw[33:37], "big")
    if not flags & _FLAG_ATTESTED_CREDENTIAL:
        if len(raw) != 37 and not flags & _FLAG_EXTENSION_DATA:
            raise WebAuthnError("unexpected bytes after authenticator data")
        return AuthData(rp_id_hash=rp_id_hash, flags=flags, sign_count=sign_count)

    if len(raw) < 55:
        raise WebAuthnError("attested credential data is truncated")
    aaguid = raw[37:53]
    credential_id_length = int.from_bytes(raw[53:55], "big")
    if not 0 < credential_id_length <= _CREDENTIAL_ID_MAX_BYTES:
        raise WebAuthnError("invalid credential ID length")
    key_offset = 55 + credential_id_length
    if key_offset > len(raw):
        raise WebAuthnError("credential ID is truncated")
    credential_id = raw[55:key_offset]
    try:
        key, consumed = _decode_cbor_prefix(raw[key_offset:])
    except WebAuthnError:
        raise WebAuthnError("credential public key is malformed") from None
    if not isinstance(key, dict):
        raise WebAuthnError("credential public key is malformed")
    trailing = raw[key_offset + consumed :]
    if trailing and not flags & _FLAG_EXTENSION_DATA:
        raise WebAuthnError("unexpected bytes after credential public key")
    return AuthData(
        rp_id_hash=rp_id_hash,
        flags=flags,
        sign_count=sign_count,
        aaguid=aaguid,
        credential_id=credential_id,
        credential_public_key=raw[key_offset : key_offset + consumed],
    )


# -- Credential keys -------------------------------------------------------


def public_key_from_cose(cose: bytes) -> tuple[Any, int]:
    """Return the verification key and COSE algorithm for a credential key."""
    key = decode_cbor(cose)
    if not isinstance(key, dict):
        raise WebAuthnError("credential public key is malformed")
    key_type = key.get(1)
    algorithm = key.get(3)
    if not isinstance(key_type, int) or isinstance(key_type, bool):
        raise WebAuthnError("credential key type is missing")
    if not isinstance(algorithm, int) or isinstance(algorithm, bool):
        raise WebAuthnError("credential algorithm is missing")

    if key_type == 2:
        if algorithm != _ES256 or key.get(-1) != 1:
            raise WebAuthnError("unsupported elliptic curve key")
        x, y = key.get(-2), key.get(-3)
        if not isinstance(x, bytes) or not isinstance(y, bytes):
            raise WebAuthnError("elliptic curve point is missing")
        if len(x) != 32 or len(y) != 32:
            raise WebAuthnError("invalid elliptic curve point")
        try:
            public_key = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), b"\x04" + x + y
            )
        except ValueError:
            raise WebAuthnError("invalid elliptic curve point") from None
        return public_key, algorithm

    if key_type == 3:
        if algorithm != _RS256:
            raise WebAuthnError("unsupported RSA algorithm")
        modulus, exponent = key.get(-1), key.get(-2)
        if not isinstance(modulus, bytes) or not isinstance(exponent, bytes):
            raise WebAuthnError("RSA key is missing")
        if not 0 < len(modulus) <= 1024 or not 0 < len(exponent) <= 8:
            raise WebAuthnError("invalid RSA key")
        numbers = rsa.RSAPublicNumbers(
            int.from_bytes(exponent, "big"), int.from_bytes(modulus, "big")
        )
        try:
            return numbers.public_key(), algorithm
        except ValueError:
            raise WebAuthnError("invalid RSA key") from None

    if key_type == 1:
        if algorithm != _EDDSA or key.get(-1) != 6:
            raise WebAuthnError("unsupported EdDSA key")
        raw_point = key.get(-2)
        if not isinstance(raw_point, bytes) or len(raw_point) != 32:
            raise WebAuthnError("invalid EdDSA key")
        return ed25519.Ed25519PublicKey.from_public_bytes(raw_point), algorithm

    raise WebAuthnError("unsupported credential key type")


def verify_signature(key: Any, algorithm: int, signature: bytes, message: bytes) -> bool:
    try:
        if algorithm == _ES256:
            cast(ec.EllipticCurvePublicKey, key).verify(
                signature, message, ec.ECDSA(hashes.SHA256())
            )
        elif algorithm == _RS256:
            cast(rsa.RSAPublicKey, key).verify(
                signature, message, padding.PKCS1v15(), hashes.SHA256()
            )
        elif algorithm == _EDDSA:
            cast(ed25519.Ed25519PublicKey, key).verify(signature, message)
        else:
            return False
    except InvalidSignature:
        return False
    return True


# -- Ceremonies ------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredCredential:
    credential_id: bytes
    public_key: bytes
    algorithm: int
    sign_count: int
    aaguid: bytes


def verify_registration(
    client_data_json: bytes,
    attestation_object: bytes,
    *,
    challenge: str,
    rp_id: str,
    origin: str,
) -> RegisteredCredential:
    data = _client_data(client_data_json)
    _require_client_data(data, kind="webauthn.create", challenge=challenge, origin=origin)

    attestation = decode_cbor(attestation_object)
    if not isinstance(attestation, dict):
        raise WebAuthnError("attestation object is malformed")
    fmt = attestation.get("fmt")
    if not isinstance(fmt, str) or not isinstance(attestation.get("attStmt"), dict):
        raise WebAuthnError("attestation object is malformed")
    raw_auth_data = attestation.get("authData")
    if not isinstance(raw_auth_data, bytes):
        raise WebAuthnError("attestation object is missing authenticator data")

    auth = parse_auth_data(raw_auth_data)
    _require_rp_id(auth, rp_id)
    if not auth.user_present:
        raise WebAuthnError("the authenticator did not verify the user")
    if not auth.user_verified:
        raise WebAuthnError("the authenticator did not verify the user")
    if auth.credential_id is None or auth.credential_public_key is None:
        raise WebAuthnError("attestation object has no credential")
    _, algorithm = public_key_from_cose(auth.credential_public_key)
    return RegisteredCredential(
        credential_id=auth.credential_id,
        public_key=auth.credential_public_key,
        algorithm=algorithm,
        sign_count=auth.sign_count,
        aaguid=auth.aaguid or b"",
    )


def verify_assertion(
    client_data_json: bytes,
    authenticator_data: bytes,
    signature: bytes,
    *,
    challenge: str,
    rp_id: str,
    origin: str,
    credential_public_key: bytes,
    stored_sign_count: int,
) -> int:
    """Verify one assertion and return the authenticator's new sign counter."""
    data = _client_data(client_data_json)
    _require_client_data(data, kind="webauthn.get", challenge=challenge, origin=origin)

    auth = parse_auth_data(authenticator_data)
    _require_rp_id(auth, rp_id)
    if not auth.user_present:
        raise WebAuthnError("the authenticator did not verify the user")
    if stored_sign_count and auth.sign_count:
        if auth.sign_count <= stored_sign_count:
            raise WebAuthnError("the authenticator sign counter did not advance")

    key, algorithm = public_key_from_cose(credential_public_key)
    digest = hashlib.sha256(client_data_json).digest()
    if not verify_signature(key, algorithm, signature, authenticator_data + digest):
        raise WebAuthnError("the authenticator signature is invalid")
    return auth.sign_count


def _client_data(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > CLIENT_DATA_MAX_BYTES:
        raise WebAuthnError("client data is missing or too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WebAuthnError("client data is not valid JSON") from None
    if not isinstance(value, dict):
        raise WebAuthnError("client data is not an object")
    return cast(dict[str, Any], value)


def _require_client_data(
    data: dict[str, Any], *, kind: str, challenge: str, origin: str
) -> None:
    if data.get("type") != kind:
        raise WebAuthnError("client data has the wrong ceremony type")
    echoed = data.get("challenge")
    if not isinstance(echoed, str):
        raise WebAuthnError("client data is missing the challenge")
    try:
        supplied = b64url_decode(echoed)
        expected = b64url_decode(challenge)
    except WebAuthnError:
        raise WebAuthnError("client data has an invalid challenge") from None
    if not hmac.compare_digest(supplied, expected):
        raise WebAuthnError("client data challenge does not match")
    if data.get("origin") != origin:
        raise WebAuthnError("client data origin does not match")
    if data.get("crossOrigin") is True:
        raise WebAuthnError("cross-origin ceremonies are not allowed")


def _require_rp_id(auth: AuthData, rp_id: str) -> None:
    if not hmac.compare_digest(auth.rp_id_hash, hashlib.sha256(rp_id.encode("utf-8")).digest()):
        raise WebAuthnError("authenticator data is bound to another origin")


# -- Options ---------------------------------------------------------------


def registration_options(
    *,
    challenge: str,
    rp_id: str,
    rp_name: str,
    user_id: bytes,
    user_name: str,
    display_name: str,
    exclude: list[dict[str, Any]],
    timeout_ms: int,
) -> dict[str, Any]:
    return {
        "challenge": challenge,
        "rp": {"id": rp_id, "name": rp_name},
        "user": {
            "id": b64url_encode(user_id),
            "name": user_name,
            "displayName": display_name,
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": _ES256},
            {"type": "public-key", "alg": _RS256},
            {"type": "public-key", "alg": _EDDSA},
        ],
        "authenticatorSelection": {
            "residentKey": "preferred",
            "userVerification": "required",
        },
        "attestation": "none",
        "timeout": timeout_ms,
        "excludeCredentials": exclude,
    }


def authentication_options(
    *,
    challenge: str,
    rp_id: str,
    allow: list[dict[str, Any]],
    timeout_ms: int,
) -> dict[str, Any]:
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "allowCredentials": allow,
        "userVerification": "preferred",
        "timeout": timeout_ms,
    }
