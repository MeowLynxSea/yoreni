from __future__ import annotations

from typing import Any

import pytest

from yoreni.config.schema import Config
from yoreni.webui.settings_capabilities import (
    capability_settings_payload,
    update_api_settings,
    update_image_generation_settings,
    update_memory_settings,
    update_network_safety_settings,
    update_transcription_settings,
    update_web_search_settings,
)
from yoreni.webui.settings_contracts import WebUISettingsError
from yoreni.webui.settings_models import create_provider_settings, update_provider_settings


def test_dynamic_image_provider_selection_and_private_overrides():
    config = Config()
    name = create_provider_settings(config, {
        "name": ["SenseNova"], "apiBase": ["https://images.test/v1"],
    })
    assert update_image_generation_settings(config, {
        "provider": [name], "model": ["image-model"], "enabled": ["true"],
        "extraHeaders": ['{"X-Api-Key":"private-key"}'],
        "extraBody": ['{"watermark":false}'],
    }, oauth_status=_oauth_status)
    payload = capability_settings_payload(config, oauth_status=_oauth_status)["image_generation"]
    assert payload["provider"] == name
    assert payload["extra_headers"] == {"X-Api-Key": "••••••••"}
    assert "private-key" not in str(payload)
    row = next(row for row in payload["providers"] if row["name"] == name)
    assert row["label"] == "SenseNova" and row["configured"] and row["uses_images_api"]
    changed, reload_needed = update_provider_settings(config, {
        "provider": [name], "apiBase": ["https://updated.test/v1"],
    })
    assert changed and reload_needed
    update_image_generation_settings(config, {
        "extra_headers": ['{"X-Api-Key":"••••••••","X-Route":"images"}'],
    }, oauth_status=_oauth_status)
    assert config.tools.image_generation.extra_headers["X-Api-Key"] == "private-key"
    with pytest.raises(WebUISettingsError, match="JSON object"):
        update_image_generation_settings(config, {"extra_body": ["[]"]}, oauth_status=_oauth_status)
    config.providers.openai.api_key = "fixture"
    update_image_generation_settings(config, {"provider": ["openai"]}, oauth_status=_oauth_status)
    assert config.tools.image_generation.extra_headers is None
    assert config.tools.image_generation.extra_body is None


def test_image_provider_keeps_dynamic_id_case_and_rejects_missing_connection():
    config = Config.model_validate({"providers": {"CompanyGateway": {"apiBase": "https://images.test/v1"}}})
    update_image_generation_settings(config, {
        "provider": ["CompanyGateway"], "model": ["image"], "enabled": ["true"],
    }, oauth_status=_oauth_status)
    assert config.tools.image_generation.provider == "CompanyGateway"
    with pytest.raises(WebUISettingsError, match="unknown image generation provider"):
        update_image_generation_settings(config, {"provider": ["missing"]}, oauth_status=_oauth_status)


def _oauth_status(_spec: Any) -> dict[str, Any]:
    return {"configured": False}


def test_capability_domain_updates_representative_settings() -> None:
    config = Config()
    config.providers.openrouter.api_key = "sk-test"

    web_changed, web_restart = update_web_search_settings(
        config,
        {
            "provider": ["duckduckgo"],
            "max_results": ["7"],
            "use_jina_reader": ["false"],
        },
    )
    update_api_settings(
        config,
        {"host": ["127.0.0.2"], "port": ["8900"], "timeout": ["90"]},
    )
    image_changed = update_image_generation_settings(
        config,
        {"enabled": ["true"], "provider": ["openrouter"]},
        oauth_status=_oauth_status,
    )
    transcription_changed = update_transcription_settings(
        config,
        {"provider": ["openrouter"], "model": ["openai/whisper-large-v3"]},
    )
    network_changed, access_mode = update_network_safety_settings(
        config,
        {
            "webui_allow_local_service_access": ["false"],
            "webui_default_access_mode": ["restricted"],
        },
    )
    payload = capability_settings_payload(config, oauth_status=_oauth_status)

    assert (web_changed, web_restart) == (True, True)
    assert image_changed is True
    assert transcription_changed is True
    assert (network_changed, access_mode) == (True, "default")
    assert payload["web_search"]["max_results"] == 7
    assert payload["api"]["host"] == "127.0.0.2"
    assert payload["api"]["port"] == 8900
    assert payload["image_generation"]["enabled"] is True
    assert payload["transcription"]["provider"] == "openrouter"


def test_memory_embedding_reuses_provider_credentials():
    config = Config()
    name = create_provider_settings(config, {
        "name": ["EmbedCo"],
        "apiBase": ["https://embed.test/v1"],
        "apiKey": ["sk-embed-secret"],
    })

    assert update_memory_settings(config, {
        "enabled": ["true"],
        "embedding_enabled": ["true"],
        "embedding_provider": [name],
        "embedding_model": ["text-embedding-3-small"],
        "embedding_dimensions": ["1536"],
    })

    embedding = config.memory.embedding
    assert embedding is not None
    assert embedding.provider == name
    assert embedding.api_base is None
    assert embedding.api_key is None
    assert embedding.dimensions == 1536
    payload = capability_settings_payload(config, oauth_status=_oauth_status)["memory"]
    assert payload["enabled"] is True
    assert payload["embedding"]["enabled"] is True
    assert payload["embedding"]["provider"] == name
    assert payload["embedding"]["dimensions"] == 1536
    assert "sk-embed-secret" not in str(payload)


def test_memory_embedding_direct_endpoint_and_disable():
    config = Config()

    assert update_memory_settings(config, {
        "embedding_enabled": ["true"],
        "embedding_model": ["nomic-embed-text"],
        "embedding_api_base": ["http://127.0.0.1:11434/v1"],
        "embedding_api_key": ["local-secret"],
        "embedding_batch_size": ["16"],
        "core_token_budget": ["2000"],
        "retrieval_token_budget": ["3000"],
        "retrieval_limit": ["6"],
    })
    embedding = config.memory.embedding
    assert embedding is not None
    assert embedding.api_base == "http://127.0.0.1:11434/v1"
    assert embedding.api_key == "local-secret"
    assert embedding.batch_size == 16
    assert (config.memory.core_token_budget, config.memory.retrieval_token_budget) == (2000, 3000)
    assert config.memory.retrieval_limit == 6
    payload = capability_settings_payload(config, oauth_status=_oauth_status)["memory"]
    assert "••••" in payload["embedding"]["api_key_hint"]
    assert "local-secret" not in str(payload)

    assert update_memory_settings(config, {"embedding_enabled": ["false"]})
    assert config.memory.embedding is None


@pytest.mark.parametrize(
    "values, message",
    [
        (
            {"embedding_enabled": ["true"], "embedding_model": [""],
             "embedding_api_base": ["https://embed.test/v1"]},
            "embedding_model is required",
        ),
        (
            {"embedding_enabled": ["true"], "embedding_model": ["text-embedding-3-small"]},
            "enter an embedding API base",
        ),
        (
            {"embedding_enabled": ["true"], "embedding_model": ["text-embedding-3-small"],
             "embedding_provider": ["missing"]},
            "unknown embedding provider",
        ),
        (
            {"embedding_enabled": ["true"], "embedding_model": ["text-embedding-3-small"],
             "embedding_provider": ["openai"]},
            "has no API base",
        ),
        (
            {"embedding_enabled": ["true"], "embedding_model": ["text-embedding-3-small"],
             "embedding_api_base": ["https://embed.test/v1"],
             "embedding_dimensions": ["0"]},
            "embedding_dimensions must be between",
        ),
        (
            {"embedding_enabled": ["true"], "embedding_model": ["text-embedding-3-small"],
             "embedding_api_base": ["https://embed.test/v1"],
             "embedding_batch_size": ["128"]},
            "embedding_batch_size must be between",
        ),
        ({"retrieval_limit": ["0"]}, "retrieval_limit must be between"),
    ],
)
def test_memory_embedding_rejects_invalid_settings(values, message):
    with pytest.raises(WebUISettingsError, match=message):
        update_memory_settings(Config(), values)
