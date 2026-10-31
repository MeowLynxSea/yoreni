"""Tests for authenticator registration, sign-in, and password changes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from webauthn_fixture import (
    FLAG_ATTESTED_CREDENTIAL,
    FLAG_USER_VERIFIED,
    FakeAuthenticator,
)
from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.channels.websocket.runtime import WebSocketConfig
from yoreni.config.loader import load_config
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.onboarding_api import OnboardingError, OnboardingService
from yoreni.webui.passkeys import (
    PasskeyError,
    PasskeyService,
    PasskeyStore,
    origin_rp_id,
)
from yoreni.webui.settings_services import WebUISettingsConfig
from yoreni.webui.webauthn import WebAuthnError, b64url_decode, b64url_encode, decode_cbor
from yoreni.webui.ws_http import GatewayHTTPHandler

ORIGIN = "http://localhost:8765"
RP_ID = "localhost"


def _write_config(path: Path, password: str = "correct horse battery") -> None:
    path.write_text(
        json.dumps({
            "agents": {"defaults": {"timezone": "UTC"}},
            "channels": {"websocket": {"tokenIssueSecret": password}},
        }),
        encoding="utf-8",
    )


def _service(tmp_path: Path, password: str = "correct horse battery") -> PasskeyService:
    _write_config(tmp_path / "config.json", password)
    return PasskeyService(
        WebUISettingsConfig(tmp_path / "config.json"),
        tokens=GatewayTokenStore(),
        store=PasskeyStore(tmp_path / "passkeys.json"),
    )


def _register(service: PasskeyService, authenticator: FakeAuthenticator, name: str = "") -> dict:
    options = service.registration_options(ORIGIN, name)
    client_data, attestation = authenticator.attestation(
        options["challenge"], ORIGIN
    )
    return service.register(
        {
            "client_data_json": client_data,
            "attestation_object": attestation,
            "name": name,
            "transports": ["internal"],
        },
        ORIGIN,
    )


def _authenticate(service: PasskeyService, authenticator: FakeAuthenticator) -> dict:
    options = service.authentication_options(ORIGIN)
    client_data, auth_data, signature, credential_id = authenticator.assertion(
        options["challenge"], ORIGIN
    )
    return service.authenticate(
        {
            "client_data_json": client_data,
            "authenticator_data": auth_data,
            "signature": signature,
            "credential_id": credential_id,
        },
        ORIGIN,
    )


def test_register_then_sign_in_issues_a_bootstrap_ticket(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)

    summary = _register(service, authenticator, "This Mac")
    assert summary["name"] == "This Mac"
    assert service.list(ORIGIN)["authenticators"][0]["id"] == summary["id"]
    assert service.status(ORIGIN) == {"password_set": True, "passkey_count": 1}

    result = _authenticate(service, authenticator)
    assert result["ticket"].startswith("nbwpt_")
    assert service._tokens.check_login_ticket(result["ticket"]) is True
    assert service.list(ORIGIN)["authenticators"][0]["last_used_at"] is not None


def test_sign_in_rejects_a_stale_signature_and_a_replayed_challenge(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)
    _register(service, authenticator)

    options = service.authentication_options(ORIGIN)
    client_data, auth_data, signature, credential_id = authenticator.assertion(
        options["challenge"], ORIGIN
    )
    tampered = b64url_encode(b64url_decode(signature)[:-1] + b"\x00")
    with pytest.raises(PasskeyError, match="signature"):
        service.authenticate(
            {
                "client_data_json": client_data,
                "authenticator_data": auth_data,
                "signature": tampered,
                "credential_id": credential_id,
            },
            ORIGIN,
        )
    # The failed attempt consumed the challenge.
    with pytest.raises(PasskeyError, match="expired"):
        service.authenticate(
            {
                "client_data_json": client_data,
                "authenticator_data": auth_data,
                "signature": signature,
                "credential_id": credential_id,
            },
            ORIGIN,
        )


def test_registration_rejects_another_origin_and_a_duplicate_credential(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)

    options = service.registration_options(ORIGIN)
    client_data, attestation = authenticator.attestation(options["challenge"], "http://evil.example")
    with pytest.raises(PasskeyError, match="origin"):
        service.register(
            {"client_data_json": client_data, "attestation_object": attestation},
            ORIGIN,
        )

    _register(service, authenticator)
    options = service.registration_options(ORIGIN)
    client_data, attestation = authenticator.attestation(options["challenge"], ORIGIN)
    with pytest.raises(PasskeyError, match="already registered"):
        service.register(
            {"client_data_json": client_data, "attestation_object": attestation},
            ORIGIN,
        )


def test_user_presence_is_required_at_registration(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)

    options = service.registration_options(ORIGIN)
    client_data, attestation = authenticator.attestation(
        options["challenge"],
        ORIGIN,
        flags=FLAG_USER_VERIFIED | FLAG_ATTESTED_CREDENTIAL,
    )
    with pytest.raises(PasskeyError, match="verify the user"):
        service.register(
            {"client_data_json": client_data, "attestation_object": attestation},
            ORIGIN,
        )


def test_sign_in_rejects_a_regressing_counter_but_accepts_a_zero_counter(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)
    authenticator.sign_count = 7
    _register(service, authenticator)

    options = service.authentication_options(ORIGIN)
    client_data, auth_data, signature, credential_id = authenticator.assertion(
        options["challenge"], ORIGIN, sign_count=3
    )
    with pytest.raises(PasskeyError, match="sign counter"):
        service.authenticate(
            {
                "client_data_json": client_data,
                "authenticator_data": auth_data,
                "signature": signature,
                "credential_id": credential_id,
            },
            ORIGIN,
        )

    # Platform authenticators that never increment the counter report zero.
    authenticator.sign_count = 0
    assert _authenticate(service, authenticator)["ticket"].startswith("nbwpt_")


def test_passkeys_are_bound_to_the_registering_host(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)
    _register(service, authenticator)

    assert service.status("http://127.0.0.1:8765")["passkey_count"] == 0
    # Credentials are scoped by host, not port, as browsers do.
    assert service.status("http://localhost:9000")["passkey_count"] == 1


def test_origin_rp_id_requires_a_domain(tmp_path: Path) -> None:
    assert origin_rp_id("https://yoreni.example.com") == "yoreni.example.com"
    assert origin_rp_id("http://localhost:8765") == "localhost"
    with pytest.raises(PasskeyError):
        origin_rp_id("http://192.168.1.10:8765")
    with pytest.raises(PasskeyError):
        origin_rp_id("http://[::1]:8765")


def test_cbor_decoder_rejects_unsupported_encodings() -> None:
    with pytest.raises(WebAuthnError):
        decode_cbor(b"\x9f\x01\xff")  # indefinite array
    with pytest.raises(WebAuthnError):
        decode_cbor(b"\xc0\x01")  # tagged value
    with pytest.raises(WebAuthnError):
        decode_cbor(b"\xa2\x01\x02\x01\x03")  # duplicate map key
    with pytest.raises(WebAuthnError):
        decode_cbor(b"\x01\x02")  # trailing bytes


def test_password_change_requires_the_current_password(tmp_path: Path) -> None:
    _write_config(tmp_path / "config.json", "correct horse battery")
    live = WebSocketConfig(token_issue_secret="correct horse battery")
    service = OnboardingService(
        WebUISettingsConfig(tmp_path / "config.json"),
        workspace=tmp_path / "workspace",
        live_websocket=live,
    )

    with pytest.raises(OnboardingError) as wrong:
        service.change_password("nope", "a new password")
    assert wrong.value.status == 403

    service.change_password("correct horse battery", "a new password")
    assert live.token_issue_secret == "a new password"
    assert (
        load_config(tmp_path / "config.json").channels.websocket["tokenIssueSecret"]
        == "a new password"
    )


def test_login_tickets_are_distinct_from_issued_tokens() -> None:
    tokens = GatewayTokenStore()
    ticket = tokens.issue_login_ticket(60)
    assert tokens.check_login_ticket(ticket) is True
    assert tokens.take_issued_token_audience(ticket) is None
    tokens.revoke_login_tickets()
    assert tokens.check_login_ticket(ticket) is False


def test_status_uses_the_page_origin_when_a_proxy_rewrites_host(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register(service, FakeAuthenticator(RP_ID))

    handler = object.__new__(GatewayHTTPHandler)
    handler.passkeys = service
    request = Request(
        "/api/auth/status?origin=http%3A%2F%2Flocalhost%3A5173",
        Headers({"Host": "127.0.0.1:8765"}),
    )
    payload = json.loads(handler._handle_auth_status(request).body)
    assert payload["passkey_count"] == 1


def test_options_accept_the_page_origin_through_a_rewriting_proxy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)
    _register(service, authenticator)

    handler = object.__new__(GatewayHTTPHandler)
    handler.passkeys = service
    request = Request(
        "/api/auth/passkey/options?origin=http%3A%2F%2Flocalhost%3A5173",
        Headers({"Host": "127.0.0.1:8765"}),
    )
    response = handler._handle_passkey_options(request)
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["rpId"] == RP_ID
    assert payload["allowCredentials"]


def test_passkey_document_is_private_and_protected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    authenticator = FakeAuthenticator(RP_ID)
    _register(service, authenticator, "This Mac")

    path = tmp_path / "passkeys.json"
    assert path.stat().st_mode & 0o777 == 0o600
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert len(document["user_handle"]) == 32
