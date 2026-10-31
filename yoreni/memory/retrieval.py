"""Hybrid retrieval over stored facts.

Keyword (FTS5) and semantic (exact cosine) rankings are fused with Reciprocal
Rank Fusion. Every result carries its status, time validity and evidence
references, so the model receives data rather than an anonymous summary.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

from typing import Any

from loguru import logger

from yoreni.memory.embeddings import (
    EmbeddingClient,
    cosine_similarity,
    embedding_index_key,
)
from yoreni.memory.models import (
    FactStatus,
    MemoryHit,
    MemoryRetrievalError,
    SearchFilter,
    fts_match_query,
    normalize_text,
)
from yoreni.memory.store import FactStore

RRF_K = 60
CANDIDATE_MULTIPLIER = 4
MIN_CANDIDATES = 20


class Retriever:
    """Serve one query from every configured index and fuse the rankings."""

    def __init__(self, store: FactStore, embedder: EmbeddingClient | None = None) -> None:
        self.store = store
        self.embedder = embedder

    async def search(self, request: SearchFilter) -> list[MemoryHit]:
        query = request.normalized()
        where, params = _where_clause(query)
        candidates = max(MIN_CANDIDATES, query.limit * CANDIDATE_MULTIPLIER)

        fts_ranking: list[str] = []
        match = fts_match_query(query.query)
        if match is not None:
            fts_ranking = [
                fact_id
                for fact_id, _rank in self.store.search_fts(
                    match, where=where, params=params, limit=candidates,
                )
            ]

        vector_ranking: list[str] = []
        embedder = self.embedder
        if embedder is not None:
            index_key = embedding_index_key(embedder.settings)
            try:
                query_vector = await embedder.embed_query(query.query)
            except Exception as exc:
                # A failed embedding call must not be reported as an empty
                # result, which would look like "the user never said that".
                raise MemoryRetrievalError(
                    f"semantic retrieval failed: {type(exc).__name__}: {exc}"
                ) from exc
            scored: list[tuple[float, str]] = []
            for fact, vector in self.store.vector_candidates(
                index_key, where=where, params=params,
            ):
                score = cosine_similarity(query_vector, vector)
                if score > 0.0:
                    scored.append((score, fact.id))
            scored.sort(key=lambda item: (-item[0], item[1]))
            vector_ranking = [fact_id for _score, fact_id in scored[:candidates]]

        fused: dict[str, float] = {}
        signals: dict[str, list[str]] = {}
        for ranking, label in ((fts_ranking, "keyword"), (vector_ranking, "semantic")):
            for rank, fact_id in enumerate(ranking, start=1):
                fused[fact_id] = fused.get(fact_id, 0.0) + 1.0 / (RRF_K + rank)
                signals.setdefault(fact_id, []).append(label)

        if not fused:
            return []

        facts = self.store.facts_by_ids(fused)
        ordered = sorted(
            (
                (fused[fact_id], fact)
                for fact_id, fact in facts.items()
                if fact.status is not FactStatus.DELETED
            ),
            key=lambda item: (
                -item[0],
                item[1].observed_at or "",
                item[1].id,
            ),
        )[:query.limit]

        sources = self.store.sources_for(fact.id for _score, fact in ordered)
        hits: list[MemoryHit] = []
        seen: set[tuple[str, str, str]] = set()
        for score, fact in ordered:
            identity = (fact.subject, fact.predicate, normalize_text(fact.text))
            if identity in seen:
                continue
            seen.add(identity)
            hits.append(MemoryHit(
                fact=fact,
                score=score,
                signals=tuple(signals.get(fact.id, ())),
                sources=tuple(sources.get(fact.id, ())),
            ))
        logger.debug(
            "memory retrieval: query={!r} keyword={} semantic={} fused={}",
            query.query[:80], len(fts_ranking), len(vector_ranking), len(hits),
        )
        return hits

    def fact_detail(self, fact_id: str) -> dict[str, Any] | None:
        fact = self.store.fact(fact_id)
        if fact is None:
            return None
        sources = self.store.sources_for([fact_id]).get(fact_id, [])
        return {
            "fact": fact.to_dict(),
            "sources": [source.to_dict() for source in sources],
            "relations": self.store.relations_for(fact_id),
            "versions": [
                version.to_dict() for version in self.store.versions_for(fact)
            ],
        }


def _where_clause(request: SearchFilter) -> tuple[str, list[Any]]:
    clauses: list[str] = ["facts.status != 'deleted'"]
    params: list[Any] = []
    if request.scope:
        clauses.append("(facts.scope = ? OR facts.scope = 'global')")
        params.append(request.scope)
    if request.statuses:
        placeholders = ",".join("?" for _ in request.statuses)
        clauses.append(f"facts.status IN ({placeholders})")
        params.extend(status.value for status in request.statuses)
    if request.kinds:
        placeholders = ",".join("?" for _ in request.kinds)
        clauses.append(f"facts.kind IN ({placeholders})")
        params.extend(kind.value for kind in request.kinds)
    if request.subjects:
        placeholders = ",".join("?" for _ in request.subjects)
        clauses.append(f"facts.subject IN ({placeholders})")
        params.extend(request.subjects)
    if request.valid_at:
        clauses.append("facts.valid_from <= ?")
        params.append(request.valid_at)
        clauses.append("(facts.valid_to IS NULL OR facts.valid_to > ?)")
        params.append(request.valid_at)
    return " AND ".join(clauses), params


def require_embedding(clients: EmbeddingClient | None) -> EmbeddingClient:
    if clients is None:
        raise MemoryRetrievalError(
            "semantic retrieval is unavailable: memory.embedding is not configured"
        )
    return clients


__all__ = ["RRF_K", "Retriever", "require_embedding"]
