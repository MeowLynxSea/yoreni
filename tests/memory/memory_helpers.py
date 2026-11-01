"""Helpers shared by long-term memory tests (no pytest fixtures here)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from yoreni.memory.models import FactDraft
from yoreni.memory.service import MemoryService
from yoreni.providers.base import GenerationSettings, LLMProvider, LLMResponse


class ScriptedProvider(LLMProvider):
    """Provider whose chat responses are supplied by the test, in order."""

    def __init__(self, *responses: LLMResponse | Callable[[list[dict[str, Any]]], LLMResponse]):
        super().__init__(provider_name="scripted")
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.options: list[dict[str, Any]] = []
        self.generation = GenerationSettings(temperature=0.0, max_tokens=512)

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        self.options.append(kwargs)
        if not self._responses:
            raise AssertionError("scripted provider has no response left")
        response = self._responses.pop(0)
        return response(messages) if callable(response) else response


class TopicEmbedder:
    """Deterministic embedder mapping configured keywords onto topic vectors.

    Retrieval tests use it to prove that a semantic-only match is possible
    without a network embedding service or an ANN index.
    """

    def __init__(
        self,
        topics: dict[str, list[float]],
        *,
        model: str = "test-embedding",
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.topics = topics
        self.aliases = aliases or {}
        self.settings = SimpleNamespace(
            model=model, dimensions=len(next(iter(topics.values()))),
        )

    def vector(self, text: str) -> list[float]:
        lowered = text.casefold()
        dims = self.settings.dimensions
        scores = [0.0] * dims
        matched = False
        for topic, vector in self.topics.items():
            names = {topic, topic.casefold(), *(
                alias for alias, target in self.aliases.items() if target == topic
            )}
            if any(name.casefold() in lowered for name in names):
                matched = True
                scores = [left + right for left, right in zip(scores, vector, strict=True)]
        if not matched:
            # Unmatched text lands on a dedicated orthogonal direction so it
            # never accidentally outranks a real semantic match.
            scores[-1] = 1.0
        norm = sum(value * value for value in scores) ** 0.5
        return [value / norm for value in scores] if norm else scores

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self.vector(text)


@dataclass(frozen=True)
class MemoryHarness:
    service: MemoryService
    workspace: Path

    def record(
        self,
        run_id: str,
        *messages: tuple[str, str],
        observed_at: str | None = None,
    ) -> None:
        payload = [{"role": role, "content": content} for role, content in messages]
        self.service.record_run(run_id, payload, observed_at=observed_at)

    def draft(self, **kwargs: Any) -> FactDraft:
        kwargs.setdefault("subject", "user")
        kwargs.setdefault("predicate", "preferred_language")
        kwargs.setdefault("text", "The user prefers Chinese.")
        kwargs.setdefault("kind", "user_preference")
        kwargs.setdefault("source_type", "user_statement")
        return FactDraft(**kwargs)

    def evidence_id(self, run_id: str, role: str = "user", index: int = 0) -> str:
        rows = [item for item in self.service.store.evidence_for_run(run_id) if item.role == role]
        return rows[index].id

    def commit(self, run_id: str, **kwargs: Any) -> str:
        """Record one user statement and commit a fact citing it."""
        kwargs.setdefault("evidence_text", "remember this")
        text = kwargs.pop("evidence_text")
        self.record(run_id, ("user", text))
        source = self.evidence_id(run_id)
        return self.service.store.commit_candidates([
            self.draft(sources=(source,), **kwargs),
        ])[0]


def facts_payload(*facts: dict[str, Any]) -> str:
    return json.dumps({"facts": list(facts)}, ensure_ascii=False)


def tokenize(text: str) -> list[str]:
    """Mirror the CJK tokenization contract for assertions on index text."""
    return re.findall(r"\w+", text.casefold())
