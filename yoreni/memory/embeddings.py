"""Embedding client for semantic retrieval.

The client speaks the OpenAI-compatible ``/embeddings`` protocol. There is no
fallback model: a configured embedding endpoint either answers with vectors of
the configured shape or the caller receives an error.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

import httpx
from loguru import logger

from yoreni.memory.models import MemoryRetrievalError

REQUEST_TIMEOUT_S = 30.0
_MAX_BATCH = 64


class EmbeddingError(MemoryRetrievalError):
    """The configured embedding endpoint did not return usable vectors."""


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    """Explicit identity of the embedding index this workspace uses."""

    model: str
    api_base: str
    api_key: str | None = None
    dimensions: int | None = None
    batch_size: int = 32
    timeout_s: float = REQUEST_TIMEOUT_S

    @property
    def signature(self) -> str:
        dims = "auto" if self.dimensions is None else str(self.dimensions)
        return f"{self.model}:{dims}"


def embedding_index_key(settings: object) -> str:
    """Identify one embedding index by model and configured dimension.

    Vectors from a different model or dimension are a different index; mixing
    them would silently score zero and look like "no match".
    """
    model = str(getattr(settings, "model", "") or "")
    dimensions = getattr(settings, "dimensions", None)
    return f"{model}:{int(dimensions) if dimensions is not None else 'auto'}"


def resolve_embedding_settings(
    embedding_config: object,
    providers: object,
) -> EmbeddingSettings | None:
    """Resolve the configured embedding endpoint without inventing defaults.

    ``embedding_config`` is a :class:`yoreni.config.schema.EmbeddingConfig`
    or ``None``. A provider entry supplies ``api_base``/``api_key``; a direct
    ``api_base`` on the embedding block wins.
    """
    if embedding_config is None:
        return None
    model = str(getattr(embedding_config, "model", "") or "").strip()
    if not model:
        raise EmbeddingError("memory.embedding.model is required when embeddings are enabled")
    api_base = str(getattr(embedding_config, "api_base", "") or "").strip()
    api_key = getattr(embedding_config, "api_key", None)
    provider_name = str(getattr(embedding_config, "provider", "") or "").strip()
    if not api_base:
        if not provider_name:
            raise EmbeddingError(
                "memory.embedding requires api_base or a provider name"
            )
        provider = getattr(providers, provider_name, None)
        if provider is None:
            raise EmbeddingError(
                f"memory.embedding.provider {provider_name!r} is not configured"
            )
        api_base = str(getattr(provider, "api_base", "") or "").strip()
        if not api_base:
            raise EmbeddingError(
                f"provider {provider_name!r} has no api_base for embeddings"
            )
        if api_key is None:
            api_key = getattr(provider, "api_key", None)
    dimensions = getattr(embedding_config, "dimensions", None)
    if dimensions is not None and int(dimensions) <= 0:
        raise EmbeddingError("memory.embedding.dimensions must be positive")
    return EmbeddingSettings(
        model=model,
        api_base=api_base.rstrip("/"),
        api_key=str(api_key) if api_key else None,
        dimensions=int(dimensions) if dimensions is not None else None,
        batch_size=max(1, min(int(getattr(embedding_config, "batch_size", 32) or 32), _MAX_BATCH)),
    )


class EmbeddingClient:
    """Async OpenAI-compatible embedding client with strict response checks."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.settings.batch_size):
            batch = texts[start:start + self.settings.batch_size]
            vectors.extend(await self._request(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        if len(vectors) != 1:
            raise EmbeddingError("embedding endpoint returned no vector for the query")
        return vectors[0]

    async def _request(self, batch: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.settings.model, "input": batch}
        if self.settings.dimensions is not None:
            payload["dimensions"] = self.settings.dimensions
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        url = f"{self.settings.api_base}/embeddings"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_s, transport=self._transport,
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise EmbeddingError(
                f"embedding endpoint returned {response.status_code}: {response.text[:300]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingError("embedding endpoint returned non-JSON output") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("embedding endpoint returned the wrong number of vectors")
        ordered = sorted(
            (cast(dict[str, Any], item) for item in cast(list[Any], data)),
            key=lambda item: int(item.get("index", 0)),
        )
        vectors: list[list[float]] = []
        for item in ordered:
            raw = item.get("embedding")
            if not isinstance(raw, list) or not raw:
                raise EmbeddingError("embedding endpoint returned an empty vector")
            vector = [_finite_float(value) for value in cast(list[Any], raw)]
            if self.settings.dimensions is not None and len(vector) != self.settings.dimensions:
                raise EmbeddingError(
                    f"embedding dimension {len(vector)} does not match configured "
                    f"{self.settings.dimensions}"
                )
            vectors.append(vector)
        dims = {len(vector) for vector in vectors}
        if len(dims) != 1:
            raise EmbeddingError("embedding endpoint returned inconsistent dimensions")
        logger.debug("embedded {} texts with {} (dim {})", len(batch), self.settings.model, dims.pop())
        return vectors


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmbeddingError("embedding vector contains a non-numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise EmbeddingError("embedding vector contains a non-finite value")
    return number


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Exact cosine similarity; both vectors must have the same dimension."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


__all__ = [
    "EmbeddingClient",
    "EmbeddingError",
    "EmbeddingSettings",
    "cosine_similarity",
    "resolve_embedding_settings",
]
