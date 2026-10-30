from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoreni.agent.state import AgentStore
from yoreni.channels.websocket.runtime import WebSocketConfig
from yoreni.config.loader import load_config, save_config
from yoreni.webui.onboarding_api import OnboardingError, OnboardingService
from yoreni.webui.settings_services import WebUISettingsConfig


def _write_config(path: Path, *, with_model: bool = False) -> None:
    defaults: dict[str, object] = {"timezone": "UTC"}
    data: dict[str, object] = {"agents": {"defaults": defaults}}
    if with_model:
        defaults.update({"provider": "openai", "model": "gpt-4o"})
        data["providers"] = {"openai": {"api_key": "sk-test"}}
    path.write_text(json.dumps(data), encoding="utf-8")


def _service(tmp_path: Path, live: WebSocketConfig | None = None) -> OnboardingService:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return OnboardingService(
        WebUISettingsConfig(tmp_path / "config.json"),
        workspace=workspace,
        live_websocket=live,
    )


def test_onboarding_requires_token_and_model_then_creates_welcome_once(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path)
    live = WebSocketConfig()
    service = _service(tmp_path, live)

    assert service.snapshot()["status"] == "uninitialized"
    assert service.snapshot()["step"] == "token"

    with pytest.raises(OnboardingError):
        service.set_token("short")

    service.set_token("correct horse battery")
    assert live.token_issue_secret == "correct horse battery"
    assert load_config(config_path).channels.websocket["tokenIssueSecret"] == "correct horse battery"
    assert service.snapshot()["token_set"] is True

    service.save_profile({
        "name": "Ada",
        "birthday": "1990-12-10",
        "gender": "female",
        "style": {"base": "friendly", "affability": "more"},
        "notes": "Prefer short answers.",
    })
    service.save_step("model")

    # A fresh instance sees the same progress after a restart.
    restarted = _service(tmp_path)
    assert restarted.snapshot()["step"] == "model"
    assert restarted.snapshot()["profile"]["name"] == "Ada"

    with pytest.raises(OnboardingError, match="Model setup is incomplete"):
        restarted.complete("en")

    config = load_config(config_path)
    config.agents.defaults.provider = "openai"
    config.agents.defaults.model = "gpt-4o"
    config.providers.openai.api_key = "sk-test"
    save_config(config, config_path)

    first = restarted.complete("en")
    assert first["status"] == "completed"
    assert first["created_welcome"] is True
    assert restarted.is_complete() is True

    history = AgentStore(tmp_path / "workspace").webui_history()
    assert len(history) == 1
    assert history[0]["kind"] == "internal"
    entries = history[0]["entries"]
    assert [entry["payload"]["content"] for entry in entries] == [
        "Hey Ada, I'm Yoreni",
        "I can manage schedules for life, work, and study, help with research, or prepare any report",
        "We can start wherever you like",
    ]

    second = restarted.complete("en")
    assert second["created_welcome"] is False
    assert len(AgentStore(tmp_path / "workspace").webui_history()[0]["entries"]) == 3


def test_settings_profile_update_requires_name(tmp_path: Path) -> None:
    _write_config(tmp_path / "config.json")
    service = _service(tmp_path)

    with pytest.raises(OnboardingError, match="name is required"):
        service.update_profile({"name": "", "style": {"base": "default"}})

    updated = service.update_profile({
        "name": "Ada",
        "style": {"base": "pragmatic", "structure": "more"},
    })
    assert updated["style"]["base"] == "pragmatic"
    assert service.read_profile()["name"] == "Ada"


def test_completed_install_with_existing_token_and_model_is_not_onboarded(tmp_path: Path) -> None:
    _write_config(tmp_path / "config.json", with_model=True)
    config = load_config(tmp_path / "config.json")
    config.channels.websocket = {
        "enabled": True,
        "tokenIssueSecret": "already-chosen",
    }
    save_config(config, tmp_path / "config.json")

    service = _service(tmp_path)

    assert service.snapshot()["status"] == "completed"
    assert service.is_complete() is True


@pytest.mark.asyncio
async def test_setup_http_routes_return_state_and_validation_errors(tmp_path: Path) -> None:
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from yoreni.webui.ws_http import GatewayHTTPHandler

    _write_config(tmp_path / "config.json")
    service = _service(tmp_path)
    handler = object.__new__(GatewayHTTPHandler)
    handler.onboarding = service
    handler.check_api_token = lambda _request: True
    request = Request("/api/onboarding", Headers())
    setattr(request, "_yoreni_trusted_proxy_authenticated", True)

    response = await handler._handle_setup(request, "/api/onboarding")
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "uninitialized"

    setattr(request, "_yoreni_webui_mutation_payload", {"token": "tiny"})
    response = await handler._handle_setup(request, "/api/onboarding/token")
    assert response.status_code == 400
    assert b"between 8 and 256" in response.body

    setattr(request, "_yoreni_webui_mutation_payload", {"token": "a long enough password"})
    response = await handler._handle_setup(request, "/api/onboarding/token")
    assert response.status_code == 200
    assert json.loads(response.body)["token_set"] is True

    response = await handler._handle_setup(request, "/api/profile")
    assert response.status_code == 200
    assert json.loads(response.body)["profile"]["name"] == ""
