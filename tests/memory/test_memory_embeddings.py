"""Embedding configuration and strict response validation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from yoreni.config.schema import EmbeddingConfig, MemoryConfig
from yoreni.memory.embeddings import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingSettings,
    cosine_similarity,
    embedding_index_key,
    resolve_embedding_settings,
)


def test_embedding_settings_reuse_a_named_provider():
    providers = SimpleNamespace(
        openai=SimpleNamespace(api_base="https://api.example/v1/", api_key="secret"),
    )
    settings = resolve_embedding_settings(
        EmbeddingConfig(provider="openai", model="text-embedding-3-small"), providers,
    )

    assert settings is not None
    assert settings.api_base == "https://api.example/v1"
    assert settings.api_key == "secret"
    assert settings.signature == "text-embedding-3-small:auto"


def test_embedding_settings_require_a_real_endpoint():
    with pytest.raises(ValueError, match="api_base or provider"):
        EmbeddingConfig(model="text-embedding-3-small")

    with pytest.raises(EmbeddingError, match="not configured"):
        resolve_embedding_settings(
            EmbeddingConfig(provider="missing", model="m"), SimpleNamespace(),
        )


def test_embedding_config_is_optional_keyword_only_memory():
    config = MemoryConfig()
    assert config.embedding is None
    assert resolve_embedding_settings(config.embedding, None) is None


def test_embedding_index_key_separates_models_and_dimensions():
    assert embedding_index_key(SimpleNamespace(model="m", dimensions=None)) == "m:auto"
    assert embedding_index_key(SimpleNamespace(model="m", dimensions=8)) == "m:8"
    assert embedding_index_key(SimpleNamespace(model="n", dimensions=8)) == "n:8"


def _client(handler) -> EmbeddingClient:
    return EmbeddingClient(
        EmbeddingSettings(model="m", api_base="https://embed.example/v1", dimensions=3),
        transport=httpx.MockTransport(handler),
    )


async def test_vectors_are_returned_in_input_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 1.0, 0.0]},
            {"index": 0, "embedding": [1.0, 0.0, 0.0]},
        ]})

    vectors = await _client(handler).embed(["first", "second"])
    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


async def test_wrong_vector_count_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(EmbeddingError, match="wrong number"):
        await _client(handler).embed(["first"])


async def test_wrong_dimension_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    with pytest.raises(EmbeddingError, match="dimension"):
        await _client(handler).embed(["first"])


async def test_non_finite_values_are_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"data": [{"index": 0, "embedding": [1.0, NaN, 0.0]}]}',
            headers={"content-type": "application/json"},
        )

    with pytest.raises(EmbeddingError, match="non-finite"):
        await _client(handler).embed(["first"])


async def test_http_and_transport_failures_are_explicit():
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(EmbeddingError, match="503"):
        await _client(failing).embed(["first"])

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(EmbeddingError, match="request failed"):
        await _client(broken).embed(["first"])


def test_cosine_similarity_handles_missing_dimensions():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0


def test_multimodal_index_is_separate_from_the_text_index():
    assert embedding_index_key(SimpleNamespace(model="m", dimensions=8)) == "m:8"
    assert embedding_index_key(
        SimpleNamespace(model="m", dimensions=8, multimodal=True)
    ) == "m:8:multimodal"


async def test_documents_are_sent_as_content_objects():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [
            {"index": 0, "embedding": [1.0, 0.0, 0.0]},
            {"index": 1, "embedding": [0.0, 1.0, 0.0]},
        ]})

    vectors = await _client(handler).embed_documents([
        {"text": "a", "image": "data:image/png;base64,AAAA"},
        {"text": "b"},
    ])

    assert seen["input"] == [
        {"text": "a", "image": "data:image/png;base64,AAAA"},
        {"text": "b"},
    ]
    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert cosine_similarity([], []) == 0.0
