"""Credential storage, redaction, restoration, and auth-request behavior."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from yoreni.agent.tools.base import ToolResult
from yoreni.agent.tools.execution import _redact_tool_payload
from yoreni.credentials.auth import AuthRequestStatus
from yoreni.credentials.crypto import SecretBox
from yoreni.credentials.manager import CredentialManager, reset_credential_manager
from yoreni.credentials.models import AuthMethod
from yoreni.credentials.policy import CredentialUseDeniedError
from yoreni.credentials.store import CredentialStore
from yoreni.credentials.vault import (
    RedactionError,
    SecretVault,
    reset_vault,
)
from yoreni.providers.base import LLMProvider, LLMResponse

SECRET = "sk-live-abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def manager(tmp_path, monkeypatch) -> CredentialManager:
    import yoreni.credentials.manager as manager_module

    monkeypatch.setattr(manager_module, "_mcp_token_path", lambda: None)
    vault = SecretVault(b"\x02" * 32)
    store = CredentialStore(tmp_path / "credentials.sqlite3", box=SecretBox(b"\x01" * 32))
    instance = CredentialManager(store=store, vault=vault, auth_ttl_seconds=60)
    instance.load()
    reset_credential_manager(instance)
    reset_vault(vault)
    try:
        yield instance
    finally:
        reset_credential_manager(None)
        reset_vault(None)


def _connect(manager: CredentialManager, *, tools=("browser_type",), domains=("example.com",)):
    return manager.connect(
        "api_key",
        label="Example",
        method=AuthMethod.API_KEY,
        values={"api_key": SECRET},
        allowed_tools=tools,
        allowed_domains=domains,
        restorable=True,
    )


class RecordingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key=None, provider_name="recording")
        self.seen: list[list[dict]] = []

    async def chat(self, messages, **kwargs):  # type: ignore[override]
        self.seen.append(messages)
        return LLMResponse(content="ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "recording-model"


def test_redaction_replaces_known_secret_with_stable_placeholder(manager):
    _connect(manager)
    token = manager.vault.token_for(SECRET)

    first = manager.redact(f"key={SECRET} and again {SECRET}")
    second = manager.redact(f"{SECRET}")
    assert SECRET not in first
    assert first == f"key={token} and again {token}"
    assert second == token


def test_redaction_is_fail_closed_when_a_secret_survives(manager, monkeypatch):
    _connect(manager)
    monkeypatch.setattr(manager.vault, "redact_text", lambda value: value)
    with pytest.raises(RedactionError):
        manager.redact_messages([{"role": "user", "content": SECRET}])


async def test_provider_never_receives_the_secret(manager):
    _connect(manager)
    provider = RecordingProvider()
    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": f"my key is {SECRET}"}],
    )
    assert response.finish_reason == "stop"
    assert SECRET not in json.dumps(provider.seen, ensure_ascii=False)
    assert manager.vault.token_for(SECRET) in provider.seen[0][0]["content"]


async def test_provider_blocks_request_when_redaction_fails(manager, monkeypatch):
    _connect(manager)
    provider = RecordingProvider()
    monkeypatch.setattr(manager.vault, "redact_text", lambda value: value)
    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": SECRET}],
    )
    assert provider.seen == []
    assert response.finish_reason == "error"


def test_restoration_requires_binding_fields_and_destination(manager):
    _connect(manager)
    token = manager.vault.token_for(SECRET)
    restored = manager.restore_arguments(
        "browser_type",
        {"selector": "#password", "text": token, "url": "https://example.com/login"},
        run_id="run-1",
    )
    assert restored["text"] == SECRET

    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "browser_type",
            {"text": token, "url": "https://example.com.evil.test/login"},
            run_id="run-1",
        )
    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "browser_type",
            {"selector": token, "url": "https://example.com/login"},
            run_id="run-1",
        )
    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "shell",
            {"command": token},
            run_id="run-1",
        )
    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "some_other_tool",
            {"text": token, "url": "https://example.com/login"},
            run_id="run-1",
        )


def test_tool_output_is_redacted_before_it_reaches_context(manager):
    _connect(manager)
    result = _redact_tool_payload(ToolResult(f"password={SECRET}", is_error=False))
    assert isinstance(result, ToolResult)
    assert SECRET not in str(result)
    assert manager.vault.token_for(SECRET) in str(result)


def test_store_never_contains_plaintext_and_rewraps(tmp_path):
    store = CredentialStore(tmp_path / "db.sqlite3", box=SecretBox(b"\x03" * 32))
    manager = CredentialManager(store=store, vault=SecretVault(b"\x04" * 32))
    manager.load()
    account = manager.connect(
        "basic_auth",
        label="Example",
        method=AuthMethod.BASIC,
        values={"username": "alice", "password": "correct-horse-battery"},
        allowed_tools=("browser_type",),
        allowed_domains=("example.com",),
    )
    raw = (tmp_path / "db.sqlite3").read_bytes()
    assert b"correct-horse-battery" not in raw
    assert b"alice" not in raw

    before = store.get_account(account.id)
    assert before is not None
    ciphertext = sqlite3.connect(tmp_path / "db.sqlite3").execute(
        "SELECT payload FROM accounts WHERE id = ?", (account.id,)
    ).fetchone()[0]
    assert store.rewrap(SecretBox(b"\x05" * 32)) == 1
    after = store.get_account(account.id)
    assert after is not None and after.payload == before.payload
    new_ciphertext = sqlite3.connect(tmp_path / "db.sqlite3").execute(
        "SELECT payload FROM accounts WHERE id = ?", (account.id,)
    ).fetchone()[0]
    assert new_ciphertext != ciphertext


async def test_submitted_auth_request_creates_account_and_resumes_run(manager):
    request = manager.request_auth(
        connector_id="api_key",
        method=AuthMethod.API_KEY,
        purpose="Connect the billing service",
        run_id="run-42",
        allowed_tools=("browser_type",),
        allowed_domains=("example.com",),
    )
    wait = asyncio.create_task(manager.auth.wait(request.id, 5))
    await asyncio.sleep(0)
    account = await asyncio.to_thread(
        manager.submit_auth_request,
        request.id,
        {"api_key": "another-secret-value"},
        label="Billing",
    )
    completed = await wait
    assert completed.status is AuthRequestStatus.COMPLETED
    assert completed.account_id == account.id
    assert manager.reveal(account.id, "api_key") == "another-secret-value"

    token = manager.vault.token_for("another-secret-value")
    restored = manager.restore_arguments(
        "browser_type",
        {"text": token, "url": "https://example.com/settings"},
        run_id="run-42",
    )
    assert restored["text"] == "another-secret-value"


async def test_cancelled_auth_request_does_not_create_an_account(manager):
    request = manager.request_auth(
        connector_id="api_key",
        method=AuthMethod.API_KEY,
        purpose="Connect",
        run_id="run-1",
    )
    wait = asyncio.create_task(manager.auth.wait(request.id, 5))
    await asyncio.sleep(0)
    manager.cancel_auth_request(request.id)
    cancelled = await wait
    assert cancelled.status is AuthRequestStatus.CANCELLED
    assert manager.accounts() == []


def test_legacy_mcp_tokens_are_imported_for_redaction_without_deleting(tmp_path, monkeypatch):
    import yoreni.credentials.manager as manager_module

    legacy = tmp_path / "mcp.json"
    legacy.write_text(json.dumps({
        "version": 1,
        "servers": {
            "docs": {
                "server_fingerprint": "abc",
                "tokens": {"access_token": "legacy-access-token", "refresh_token": "legacy-refresh-token"},
            }
        },
        "generations": {},
    }), encoding="utf-8")
    monkeypatch.setattr(manager_module, "_mcp_token_path", lambda: legacy)

    store = CredentialStore(tmp_path / "db.sqlite3", box=SecretBox(b"\x06" * 32))
    vault = SecretVault(b"\x07" * 32)
    manager = CredentialManager(store=store, vault=vault)
    manager.load()

    assert legacy.exists()
    assert "legacy-access-token" not in manager.redact("legacy-access-token")
    assert any(account["connector_id"] == "mcp_server" for account in manager.accounts())


async def test_oauth_callback_rejects_unknown_state(manager):
    from yoreni.credentials.oauth import ConnectorOAuthFlowManager, OAuthFlowError

    flows = ConnectorOAuthFlowManager(manager)
    with pytest.raises(OAuthFlowError):
        await flows.handle_callback("not-a-state", "code")


def test_one_time_code_is_memory_only_and_consumed_after_use(tmp_path):
    box = SecretBox(b"\x08" * 32)
    store = CredentialStore(tmp_path / "db.sqlite3", box=box)
    vault = SecretVault(b"\x09" * 32)
    manager = CredentialManager(store=store, vault=vault)
    manager.load()

    account = manager.connect(
        "one_time_code",
        label="SMS",
        method=AuthMethod.ONE_TIME_CODE,
        values={"code": "654321"},
        allowed_tools=("browser_type",),
        allowed_domains=("example.com",),
    )
    assert b"654321" not in (tmp_path / "db.sqlite3").read_bytes()
    assert manager.account(account.id) is not None

    token = vault.token_for("654321")
    restored = manager.restore_arguments(
        "browser_type",
        {"text": token, "url": "https://example.com/verify"},
        run_id="run-1",
    )
    assert restored["text"] == "654321"
    assert manager.account(account.id) is None
    # The echo must still be redacted even though restoration is exhausted.
    assert "654321" not in manager.redact("code=654321")
    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "browser_type",
            {"text": token, "url": "https://example.com/verify"},
            run_id="run-1",
        )


def test_multi_message_context_redacts_every_occurrence(manager):
    _connect(manager)
    messages = [
        {"role": "system", "content": f"deploy key {SECRET}"},
        {"role": "user", "content": f"is this mine? {SECRET}"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "1",
                "type": "function",
                "function": {"name": "noop", "arguments": json.dumps({"key": SECRET})},
            }],
        },
        {"role": "tool", "tool_call_id": "1", "content": f"echo {SECRET}"},
    ]
    redacted = manager.redact_messages(messages)
    serialized = json.dumps(redacted, ensure_ascii=False)
    assert SECRET not in serialized
    token = manager.vault.token_for(SECRET)
    assert serialized.count(token) == 4


async def test_oauth_flow_stores_tokens_and_resumes_request(manager, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import yoreni.credentials.oauth as oauth_module
    from yoreni.credentials.models import ConnectorSpec

    manager.register_connector(ConnectorSpec(
        id="oauth_service",
        name="OAuth service",
        auth_methods=(AuthMethod.OAUTH2,),
        oauth_authorization_url="https://provider.example/authorize",
        oauth_token_url="https://provider.example/token",
        oauth_client_id="yoreni-client",
        oauth_scopes=("read",),
    ))
    request = manager.request_auth(
        connector_id="oauth_service",
        method=AuthMethod.OAUTH2,
        purpose="Connect",
        run_id="run-9",
    )
    flows = oauth_module.ConnectorOAuthFlowManager(manager)
    started = flows.start(
        "oauth_service",
        auth_request_id=request.id,
        redirect_uri="http://127.0.0.1:8765/auth/connectors/callback",
    )
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    state = query["state"][0]

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "oauth-access-value", "refresh_token": "oauth-refresh-value", "expires_in": 3600}

    class FakeClient:
        def __init__(self, **kwargs):
            _ = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            _ = url, kwargs
            return FakeResponse()

    monkeypatch.setattr(oauth_module, "validate_url_target", lambda url: url)
    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", FakeClient)

    wait = asyncio.create_task(manager.auth.wait(request.id, 5))
    await asyncio.sleep(0)
    account = await flows.handle_callback(state, "auth-code")
    completed = await wait

    assert completed.status is AuthRequestStatus.COMPLETED
    assert completed.account_id == account.id
    assert manager.reveal(account.id, "access_token") == "oauth-access-value"
    assert manager.reveal(account.id, "refresh_token") == "oauth-refresh-value"
    # OAuth material is redacted but never restorable into a tool call.
    assert manager.vault.entry_for_token(manager.vault.token_for("oauth-access-value")) is not None


async def test_execution_middleware_hides_secret_from_hooks_and_redacts_output(manager):
    from typing import Any

    from yoreni.agent.hook import AgentHook, AgentHookContext
    from yoreni.agent.tools.base import Tool
    from yoreni.agent.tools.execution import execute_tool_calls
    from yoreni.agent.tools.registry import ToolRegistry
    from yoreni.providers.base import ToolCallRequest

    class EchoTool(Tool):
        observed: list[str] = []

        @property
        def name(self) -> str:
            return "browser_type"

        @property
        def description(self) -> str:
            return "type"

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {"text": {"type": "string"}, "url": {"type": "string"}},
                "required": ["text"],
            }

        async def execute(self, **kwargs: Any) -> str:
            EchoTool.observed.append(str(kwargs.get("text")))
            return f"typed {kwargs.get('text')}"

    class RecordingHook(AgentHook):
        def __init__(self) -> None:
            super().__init__()
            self.params: list[dict[str, Any]] = []

        async def before_execute_tool(self, context, tool_call, tool, params):
            self.params.append(dict(params))

    _connect(manager)
    token = manager.vault.token_for(SECRET)
    registry = ToolRegistry()
    registry.register(EchoTool())
    hook = RecordingHook()
    context = AgentHookContext(iteration=0, messages=[])
    results, events = await execute_tool_calls(
        registry,
        [ToolCallRequest(
            id="call-1",
            name="browser_type",
            arguments={"text": token, "url": "https://example.com/login"},
        )],
        concurrent=False,
        external_lookup_counts={},
        workspace_violation_counts={},
        hook=hook,
        context=context,
        run_id="run-1",
    )
    assert EchoTool.observed == [SECRET]
    assert hook.params[0]["text"] == token
    assert SECRET not in str(results[0])
    assert SECRET not in events[0]["detail"]


def test_browser_tool_without_a_verifiable_destination_is_denied(manager):
    _connect(manager)
    token = manager.vault.token_for(SECRET)
    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "browser_type",
            {"selector": "#password", "text": token},
            run_id="run-1",
        )


def test_accounts_cannot_lend_each_others_domains(manager):
    first = manager.connect(
        "api_key",
        label="First",
        method=AuthMethod.API_KEY,
        values={"api_key": "first.example-secret"},
        allowed_tools=("browser_fill",),
        allowed_domains=("first.example",),
    )
    second = manager.connect(
        "api_key",
        label="Second",
        method=AuthMethod.API_KEY,
        values={"api_key": "second.example-secret"},
        allowed_tools=("browser_fill",),
        allowed_domains=("second.example",),
    )
    assert first.id != second.id
    mixed = (
        f"{manager.vault.token_for('first.example-secret')} "
        f"{manager.vault.token_for('second.example-secret')}"
    )
    with pytest.raises(CredentialUseDeniedError):
        manager.restore_arguments(
            "browser_fill",
            {"text": mixed, "url": "https://second.example/login"},
            run_id="run-1",
        )


async def test_provider_context_pending_messages_are_redacted(manager):
    from yoreni.providers.base import ProviderCallContext, ProviderConversationState

    _connect(manager)

    class ContextProvider(RecordingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.contexts = []

        async def chat_with_context(self, *, provider_context, **kwargs):
            self.contexts.append(provider_context)
            return await self.chat(**kwargs)

    provider = ContextProvider()
    state = ProviderConversationState(
        kind="responses", provider="openai", model="m", version=1,
        pending_messages=[{"role": "user", "content": f"secret {SECRET}"}],
    )
    await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        provider_context=ProviderCallContext(conversation_state=state),
    )
    captured = json.dumps(provider.contexts[0].conversation_state.pending_messages)
    assert SECRET not in captured
    assert manager.vault.token_for(SECRET) in captured


def test_revoked_account_loses_restoration_after_reload(tmp_path, monkeypatch):
    import yoreni.credentials.manager as manager_module

    monkeypatch.setattr(manager_module, "_mcp_token_path", lambda: None)
    store = CredentialStore(tmp_path / "db.sqlite3", box=SecretBox(b"\x0a" * 32))
    manager = CredentialManager(store=store, vault=SecretVault(b"\x0b" * 32))
    manager.load()
    account = _connect(manager)
    token = manager.vault.token_for(SECRET)
    assert manager.restore_arguments(
        "browser_type", {"text": token, "url": "https://example.com"}, run_id="r",
    )["text"] == SECRET
    manager.revoke(account.id)

    reloaded = CredentialManager(store=store, vault=SecretVault(b"\x0b" * 32))
    reloaded.load()
    with pytest.raises(CredentialUseDeniedError):
        reloaded.restore_arguments(
            "browser_type", {"text": token, "url": "https://example.com"}, run_id="r",
        )
    # Revocation removes permission, not the ability to redact an echo.
    assert SECRET not in reloaded.redact(SECRET)


def test_key_rotation_keeps_external_secrets_redacted(tmp_path, monkeypatch):
    import yoreni.credentials.crypto as crypto_module
    import yoreni.credentials.manager as manager_module

    key_path = tmp_path / "keys" / "credentials.key"
    monkeypatch.setattr(crypto_module, "master_key_path", lambda: key_path)
    monkeypatch.setattr(manager_module, "master_key_path", lambda: key_path)
    monkeypatch.delenv("YORENI_CREDENTIAL_KEY", raising=False)

    store = CredentialStore(tmp_path / "db.sqlite3", box=SecretBox(b"\x0c" * 32))
    manager = CredentialManager(store=store, vault=SecretVault(b"\x0d" * 32))
    manager.load()
    manager.register_external_secret("provider-key-abcd1234")
    assert "provider-key-abcd1234" not in manager.redact("provider-key-abcd1234")

    manager.rotate_master_key()

    assert "provider-key-abcd1234" not in manager.redact("provider-key-abcd1234")
    assert key_path.exists()


def test_placeholders_are_exposed_without_secret_values(manager):
    account = _connect(manager)
    placeholders = manager.placeholders_for_account(account.id)
    assert placeholders == {"api_key": manager.vault.token_for(SECRET)}
    assert SECRET not in json.dumps(placeholders)


def test_accounts_are_created_only_by_connects(manager):
    assert manager.accounts() == []

    account = manager.connect(
        "api_key",
        label="Example",
        method=AuthMethod.API_KEY,
        values={"api_key": SECRET},
        allowed_tools=("browser_type",),
        allowed_domains=("example.com",),
    )
    assert [item["id"] for item in manager.accounts()] == [account.id]


def test_oauth_client_id_is_persisted_for_a_connector(tmp_path, monkeypatch):
    import yoreni.credentials.manager as manager_module
    from yoreni.credentials.models import ConnectorSpec

    monkeypatch.setattr(manager_module, "_mcp_token_path", lambda: None)
    path = tmp_path / "db.sqlite3"
    store = CredentialStore(path, box=SecretBox(b"\x0e" * 32))
    manager = CredentialManager(store=store, vault=SecretVault(b"\x0f" * 32))
    manager.load()
    manager.register_connector(ConnectorSpec(
        id="oauth_demo",
        name="OAuth demo",
        auth_methods=(AuthMethod.OAUTH2,),
        oauth_authorization_url="https://example.com/authorize",
        oauth_token_url="https://example.com/token",
    ))
    updated = manager.set_oauth_client_id("oauth_demo", "client-123")
    assert updated["oauth_ready"] is True

    reloaded = CredentialManager(
        store=CredentialStore(path, box=SecretBox(b"\x0e" * 32)),
        vault=SecretVault(b"\x0f" * 32),
    )
    reloaded.load()
    demo = next(item for item in reloaded.connectors() if item["id"] == "oauth_demo")
    assert demo["oauth_ready"] is True
