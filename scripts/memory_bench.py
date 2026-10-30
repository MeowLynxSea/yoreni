"""Reproducible long-term memory benchmark; no network or model calls.

Usage:
    python -m scripts.memory_bench [--facts 2000] [--queries 200]
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from yoreni.config.schema import MemoryConfig
from yoreni.memory.dream import DreamConsolidator
from yoreni.memory.models import (
    Evidence,
    EvidenceKind,
    FactDraft,
    FactKind,
    SourceType,
    TrustLevel,
    content_hash,
    fts_match_query,
    utc_now,
)
from yoreni.memory.service import MemoryService

_WORDS = (
    "alpha", "berlin", "cache", "database", "deploy", "editor", "gateway",
    "hotel", "index", "journal", "kernel", "latency", "memory", "network",
    "object", "pattern", "query", "runner", "schema", "token", "update",
    "vector", "worker", "yaml", "zone",
)
_CJK = ("记忆", "检索", "项目", "偏好", "部署", "测试", "中文", "数据库", "网关", "模型")


class BagEmbedder:
    """Deterministic 64-dimension bag-of-words embedder for latency measurement."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(model="bench-embedding", dimensions=64)

    def vector(self, text: str) -> list[float]:
        values = [0.0] * 64
        for token in text.casefold().split():
            if token:
                values[int(content_hash(token)[:8], 16) % 64] += 1.0
        norm = sum(value * value for value in values) ** 0.5
        return [value / norm for value in values] if norm else values

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self.vector(text)


def _seed(service: MemoryService, count: int) -> list[tuple[str, str]]:
    sentences: list[tuple[str, str]] = []
    now = utc_now()
    evidence: list[Evidence] = []
    drafts: list[FactDraft] = []
    for index in range(count):
        word = _WORDS[index % len(_WORDS)]
        cjk = _CJK[index % len(_CJK)]
        text = f"The user keeps note {index} about {word} and {cjk} workflow."
        evidence_id = "ev_bench_" + content_hash(str(index))[:20]
        evidence.append(Evidence(
            id=evidence_id,
            run_id=f"bench:{index // 10}",
            event_seq=None,
            kind=EvidenceKind.USER_MESSAGE,
            role="user",
            content=text,
            content_hash=content_hash(text),
            source_type=SourceType.USER_STATEMENT,
            trust=TrustLevel.ASSERTED,
            observed_at=now,
            created_at=now,
        ))
        drafts.append(FactDraft(
            subject="user",
            predicate=f"note_{index}",
            text=text,
            kind=FactKind.USER_FACT,
            scope="global",
            sources=(evidence_id,),
            source_type=SourceType.USER_STATEMENT,
            observed_at=now,
        ))
        sentences.append((f"net{index}", text))
    service.store.insert_evidence(evidence)
    for start in range(0, len(drafts), 100):
        service.store.commit_candidates(drafts[start:start + 100])
    return sentences


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


async def _run(facts: int, queries: int) -> dict[str, float | int | str]:
    with tempfile.TemporaryDirectory() as raw:
        workspace = Path(raw)
        service = MemoryService(
            workspace, MemoryConfig(), embedder=BagEmbedder(),
        )
        started = time.perf_counter()
        sentences = _seed(service, facts)
        seed_seconds = time.perf_counter() - started

        started = time.perf_counter()
        embedded = await service.embed_missing(limit=facts)
        embed_seconds = time.perf_counter() - started

        keyword_latencies: list[float] = []
        hybrid_latencies: list[float] = []
        keyword_recall = 0
        hybrid_recall = 0
        samples = sentences[:queries]
        for index, (_key, text) in enumerate(samples):
            query = " ".join(text.split()[:9])
            match = fts_match_query(query)
            started = time.perf_counter()
            rows = service.store.search_fts(
                match,
                where="facts.status='active'",
                params=[],
                limit=8,
            ) if match else []
            keyword_latencies.append((time.perf_counter() - started) * 1000)
            found = service.store.facts_by_ids(fact_id for fact_id, _rank in rows)
            keyword_recall += int(text in [fact.text for fact in found.values()][:5])

            started = time.perf_counter()
            hybrid = await service.search(query)
            hybrid_latencies.append((time.perf_counter() - started) * 1000)
            hybrid_recall += int(text in [hit.fact.text for hit in hybrid[:5]])

        # Simulate a long-lived profile so the always-visible budget is exercised.
        (workspace / "USER.md").write_text(
            "\n".join(f"- preference {index}" for index in range(300)), encoding="utf-8",
        )
        (workspace / "memory" / "MEMORY.md").write_text(
            "\n".join(f"- background note {index}" for index in range(300)), encoding="utf-8",
        )
        block = await service.context_block(sentences[0][1]) if samples else None
        consolidator = DreamConsolidator(service, batch_facts=60)
        batch = consolidator._collect_batch()
        dream_prompt_chars = 0
        if batch is not None:
            dream_prompt_chars = len(consolidator._render_core()) + len(
                consolidator._render_facts(batch.facts)
            )

        db_path = workspace / "memory" / "memory.sqlite3"
        return {
            "facts": facts,
            "evidence": service.stats()["evidence_items"],
            "seed_seconds": round(seed_seconds, 3),
            "embed_seconds": round(embed_seconds, 3),
            "embed_chars": sum(len(text) for _key, text in sentences),
            "vectors": embedded,
            "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "keyword_p50_ms": round(_percentile(keyword_latencies, 0.50), 3),
            "keyword_p95_ms": round(_percentile(keyword_latencies, 0.95), 3),
            "hybrid_p50_ms": round(_percentile(hybrid_latencies, 0.50), 3),
            "hybrid_p95_ms": round(_percentile(hybrid_latencies, 0.95), 3),
            "keyword_recall_at_5": round(keyword_recall / max(1, len(samples)), 4),
            "hybrid_recall_at_5": round(hybrid_recall / max(1, len(samples)), 4),
            "core_chars": sum(len(content) for _name, content in service.core.sections()),
            "core_est_tokens": sum(len(content) for _name, content in service.core.sections()) // 4,
            "context_block_chars": len(block.content) if block is not None else 0,
            "context_block_est_tokens": (
                len(block.content) // 4 if block is not None else 0
            ),
            "dream_prompt_chars": dream_prompt_chars,
            "dream_prompt_est_tokens": dream_prompt_chars // 4,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=int, default=2_000)
    parser.add_argument("--queries", type=int, default=200)
    args = parser.parse_args()
    import asyncio

    from loguru import logger

    logger.remove()
    print(json.dumps(asyncio.run(_run(args.facts, args.queries)), indent=2))


if __name__ == "__main__":
    main()
