"""SQLite persistence for facts, evidence, retrieval indexes and job state.

One database file owns every long-term structure. Writes that change a fact's
meaning (insert, supersede, merge, delete) run inside a single ``BEGIN
IMMEDIATE`` transaction together with their source links and FTS rows, so a
reader never observes a fact without its index or provenance.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import json
import sqlite3
import struct
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, cast
from uuid import uuid4

from yoreni.memory.models import (
    Evidence,
    EvidenceKind,
    Fact,
    FactDraft,
    FactKind,
    FactStatus,
    JobStatus,
    MemorySchemaError,
    MemoryStats,
    MemoryStoreError,
    SourceRef,
    SourceType,
    TrustLevel,
    authority,
    content_hash,
    dedupe_key,
    fts_index_text,
    normalize_text,
    single_valued,
    utc_now,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_seq INTEGER,
    kind TEXT NOT NULL,
    role TEXT NOT NULL,
    tool_name TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_type TEXT NOT NULL,
    trust TEXT NOT NULL,
    observed_at TEXT,
    suppressed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_run ON evidence(run_id);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    value_text TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    observed_at TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    supersedes TEXT,
    superseded_by TEXT,
    confidence REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_dedupe ON facts(dedupe_key, status);
CREATE INDEX IF NOT EXISTS facts_scope ON facts(scope, status);
CREATE INDEX IF NOT EXISTS facts_subject ON facts(subject, status);

CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    quote TEXT NOT NULL,
    run_id TEXT,
    event_seq INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (fact_id, evidence_id, quote)
);
CREATE INDEX IF NOT EXISTS fact_sources_evidence ON fact_sources(evidence_id);

CREATE TABLE IF NOT EXISTS fact_relations (
    from_fact TEXT NOT NULL,
    to_fact TEXT NOT NULL,
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_fact, to_fact, relation)
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact_id UNINDEXED,
    search_text
);

CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
    evidence_id UNINDEXED,
    search_text
);

CREATE TABLE IF NOT EXISTS fact_vectors (
    fact_id TEXT PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fact_vectors_model ON fact_vectors(model, dim);

CREATE TABLE IF NOT EXISTS forget_tombstones (
    dedupe_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dedupe_key, content_hash)
);

CREATE TABLE IF NOT EXISTS memory_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    run_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_jobs_status ON memory_jobs(kind, status, created_at);

CREATE TABLE IF NOT EXISTS dream_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    fact_cursor_from INTEGER NOT NULL,
    fact_cursor_to INTEGER NOT NULL,
    proposal TEXT,
    applied TEXT,
    git_commit TEXT,
    error TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dream_runs_status ON dream_runs(status, created_at);

CREATE TABLE IF NOT EXISTS skill_proposals (
    id TEXT PRIMARY KEY,
    dream_run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class FactStore:
    """Owns the memory database; callers must not open it with another schema."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema(db)

    # -- connection ---------------------------------------------------------

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """Serialise one read-modify-write unit across processes."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        else:
            db.execute("COMMIT")
        finally:
            db.close()

    def _ensure_schema(self, db: sqlite3.Connection) -> None:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise MemorySchemaError(
                f"memory database {self.path} uses schema {version}; "
                f"this build supports {SCHEMA_VERSION}"
            )
        if version == 0:
            try:
                db.executescript(_SCHEMA)
            except sqlite3.Error as exc:
                raise MemoryStoreError(f"cannot create memory schema: {exc}") from exc
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.execute(
                "INSERT OR IGNORE INTO memory_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    # -- meta ---------------------------------------------------------------

    def meta_get(self, key: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT value FROM memory_meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def meta_set(self, key: str, value: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO memory_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # -- evidence -----------------------------------------------------------

    def insert_evidence(self, items: Iterable[Evidence]) -> int:
        """Persist observations idempotently; return how many were new."""
        rows = list(items)
        if not rows:
            return 0
        inserted = 0
        with self.connection() as db:
            for item in rows:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO evidence(
                           id,run_id,event_seq,kind,role,tool_name,content,
                           content_hash,source_type,trust,observed_at,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.id, item.run_id, item.event_seq, item.kind.value,
                        item.role, item.tool_name, item.content, item.content_hash,
                        item.source_type.value, item.trust.value, item.observed_at,
                        utc_now(),
                    ),
                )
                if int(cursor.rowcount or 0) == 0:
                    continue
                inserted += 1
                db.execute(
                    "INSERT INTO evidence_fts(evidence_id,search_text) VALUES(?,?)",
                    (item.id, fts_index_text(item.content, item.tool_name or "")),
                )
        return inserted

    def evidence(self, ids: Iterable[str]) -> dict[str, Evidence]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return {}
        placeholders = ",".join("?" for _ in wanted)
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM evidence WHERE id IN ({placeholders})", wanted,
            ).fetchall()
        return {str(row["id"]): self._evidence_from_row(row) for row in rows}

    def evidence_for_run(self, run_id: str) -> list[Evidence]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM evidence WHERE run_id=? ORDER BY rowid", (run_id,),
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def suppress_evidence(self, ids: Iterable[str]) -> int:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return 0
        placeholders = ",".join("?" for _ in wanted)
        with self.connection() as db:
            cursor = db.execute(
                f"""UPDATE evidence SET content='',suppressed=1
                    WHERE id IN ({placeholders})""",
                wanted,
            )
            db.execute(
                f"DELETE FROM fact_sources WHERE evidence_id IN ({placeholders})",
                wanted,
            )
            db.execute(
                f"DELETE FROM evidence_fts WHERE evidence_id IN ({placeholders})",
                wanted,
            )
        return int(cursor.rowcount or 0)

    def search_evidence(self, match: str, limit: int) -> list[Evidence]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT evidence.* FROM evidence_fts
                   JOIN evidence ON evidence.id=evidence_fts.evidence_id
                   WHERE evidence_fts MATCH ? AND evidence.suppressed=0
                   ORDER BY bm25(evidence_fts) LIMIT ?""",
                (match, limit),
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            event_seq=row["event_seq"],
            kind=EvidenceKind(str(row["kind"])),
            role=str(row["role"]),
            content=str(row["content"]),
            content_hash=str(row["content_hash"]),
            source_type=SourceType(str(row["source_type"])),
            trust=TrustLevel(str(row["trust"])),
            observed_at=row["observed_at"],
            created_at=str(row["created_at"]),
            tool_name=row["tool_name"],
            suppressed=bool(row["suppressed"]),
        )

    # -- facts --------------------------------------------------------------

    def fact(self, fact_id: str) -> Fact | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        return None if row is None else self._fact_from_row(row)

    def sources_for(self, fact_ids: Iterable[str]) -> dict[str, list[SourceRef]]:
        wanted = list(dict.fromkeys(fact_ids))
        result: dict[str, list[SourceRef]] = {fact_id: [] for fact_id in wanted}
        if not wanted:
            return result
        placeholders = ",".join("?" for _ in wanted)
        with self.connection() as db:
            rows = db.execute(
                f"""SELECT * FROM fact_sources WHERE fact_id IN ({placeholders})
                    ORDER BY created_at""",
                wanted,
            ).fetchall()
        for row in rows:
            result[str(row["fact_id"])].append(SourceRef(
                evidence_id=str(row["evidence_id"]),
                source_type=SourceType(str(row["source_type"])),
                quote=str(row["quote"]),
                run_id=row["run_id"],
                event_seq=row["event_seq"],
                observed_at=None,
            ))
        return result

    def relations_for(self, fact_id: str) -> list[dict[str, str]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT from_fact,to_fact,relation FROM fact_relations
                   WHERE from_fact=? OR to_fact=? ORDER BY created_at""",
                (fact_id, fact_id),
            ).fetchall()
        return [
            {"from": str(row["from_fact"]), "to": str(row["to_fact"]), "relation": str(row["relation"])}
            for row in rows
        ]

    def commit_candidates(
        self,
        drafts: Iterable[FactDraft],
        *,
        evidence: dict[str, Evidence] | None = None,
    ) -> list[str]:
        """Reconcile one extraction batch and persist it atomically.

        Every draft must cite stored, unsuppressed evidence. A draft whose
        claim cannot be reconciled with the current record is stored as
        ``disputed`` rather than overwriting what the user already established.
        """
        prepared = [draft.normalized() for draft in drafts]
        if not prepared:
            return []
        available = self.evidence(
            source for draft in prepared for source in draft.sources
        )
        if evidence:
            available.update(evidence)
        affected: list[str] = []
        with self.transaction() as db:
            for draft in prepared:
                self._verify_sources(draft, available)
                key = self._draft_key(draft)
                if self._is_tombstoned(db, key, content_hash(draft.text), draft, available):
                    continue
                affected.extend(self._apply_draft(db, draft, key, available))
        return affected

    def _verify_sources(self, draft: FactDraft, available: dict[str, Evidence]) -> None:
        if not draft.sources:
            raise MemorySchemaError(
                f"fact {draft.subject}/{draft.predicate} has no evidence reference"
            )
        for source_id in draft.sources:
            item = available.get(source_id)
            if item is None:
                raise MemorySchemaError(f"unknown evidence id {source_id!r}")
            if item.suppressed:
                raise MemorySchemaError(f"evidence {source_id!r} was deleted")

    def _draft_key(self, draft: FactDraft) -> str:
        key = dedupe_key(draft.scope, draft.subject, draft.predicate)
        if single_valued(draft.kind):
            return key
        return f"{key}|{content_hash(normalize_text(draft.text))[:16]}"

    def _current_fact(self, db: sqlite3.Connection, key: str) -> Fact | None:
        row = db.execute(
            """SELECT * FROM facts WHERE dedupe_key=?
                 AND status IN ('active','disputed','dormant')
               ORDER BY observed_at DESC, rowid DESC LIMIT 1""",
            (key,),
        ).fetchone()
        return None if row is None else self._fact_from_row(row)

    def _apply_draft(
        self,
        db: sqlite3.Connection,
        draft: FactDraft,
        key: str,
        available: dict[str, Evidence],
    ) -> list[str]:
        current = self._current_fact(db, key)
        if current is None:
            return [self._insert_fact(db, draft, key, FactStatus.ACTIVE, available)]

        if normalize_text(current.text) == normalize_text(draft.text):
            self._link_sources(db, current.id, draft, available)
            db.execute("UPDATE facts SET updated_at=? WHERE id=?", (utc_now(), current.id))
            return [current.id]

        if draft.supersedes:
            target = db.execute(
                "SELECT id FROM facts WHERE id=?", (draft.supersedes,),
            ).fetchone()
            if target is None:
                raise MemorySchemaError(
                    f"supersedes target {draft.supersedes!r} does not exist"
                )
            if draft.supersedes != current.id:
                # Correcting an older version while a different claim is
                # current is a real conflict; record it instead of guessing.
                new_id = self._insert_fact(db, draft, key, FactStatus.DISPUTED, available)
                db.execute(
                    """INSERT OR IGNORE INTO fact_relations(from_fact,to_fact,relation,created_at)
                       VALUES(?,?,'conflicts_with',?)""",
                    (new_id, current.id, utc_now()),
                )
                return [new_id]

        if self._supersedes_current(draft, current):
            new_id = self._insert_fact(db, draft, key, FactStatus.ACTIVE, available)
            self._retire(db, current, new_id, draft.valid_from or utc_now())
            db.execute(
                """INSERT OR IGNORE INTO fact_relations(from_fact,to_fact,relation,created_at)
                   VALUES(?,?,'supersedes',?)""",
                (new_id, current.id, utc_now()),
            )
            return [new_id]

        new_id = self._insert_fact(db, draft, key, FactStatus.DISPUTED, available)
        db.execute(
            """INSERT OR IGNORE INTO fact_relations(from_fact,to_fact,relation,created_at)
               VALUES(?,?,'conflicts_with',?)""",
            (new_id, current.id, utc_now()),
        )
        return [new_id]

    @staticmethod
    def _supersedes_current(draft: FactDraft, current: Fact) -> bool:
        if draft.source_type is SourceType.USER_CORRECTION:
            return True
        if draft.supersedes == current.id:
            return True
        if not single_valued(draft.kind):
            return False
        # A model inference never replaces a recorded statement; the record
        # stays authoritative until a user or a tool establishes the change.
        if authority(draft.source_type, draft.kind) < authority(current.source_type, current.kind):
            return False
        current_at = current.observed_at or ""
        draft_at = draft.observed_at or ""
        return draft_at >= current_at

    def _insert_fact(
        self,
        db: sqlite3.Connection,
        draft: FactDraft,
        key: str,
        status: FactStatus,
        available: dict[str, Evidence],
    ) -> str:
        fact_id = f"fact_{uuid4().hex[:20]}"
        now = utc_now()
        db.execute(
            """INSERT INTO facts(
                   id,subject,predicate,value,value_text,dedupe_key,scope,kind,status,
                   source_type,observed_at,valid_from,valid_to,supersedes,confidence,
                   created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fact_id, draft.subject, draft.predicate,
                _json({"text": draft.text, "attributes": draft.attributes}),
                normalize_text(draft.text), key, draft.scope, draft.kind.value,
                status.value, draft.source_type.value, draft.observed_at,
                draft.valid_from or draft.observed_at or now, draft.valid_to,
                draft.supersedes, draft.confidence, now, now,
            ),
        )
        db.execute(
            "INSERT INTO facts_fts(fact_id,search_text) VALUES(?,?)",
            (fact_id, fts_index_text(draft.subject, draft.predicate, draft.text, draft.scope)),
        )
        self._link_sources(db, fact_id, draft, available)
        return fact_id

    def _link_sources(
        self,
        db: sqlite3.Connection,
        fact_id: str,
        draft: FactDraft,
        available: dict[str, Evidence],
    ) -> None:
        for source_id in draft.sources:
            item = available[source_id]
            db.execute(
                """INSERT OR IGNORE INTO fact_sources(
                       fact_id,evidence_id,source_type,quote,run_id,event_seq,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    fact_id, source_id, item.source_type.value, item.content[:500],
                    item.run_id, item.event_seq, utc_now(),
                ),
            )
        if draft.supersedes:
            db.execute(
                """INSERT OR IGNORE INTO fact_relations(from_fact,to_fact,relation,created_at)
                   VALUES(?,?,'supersedes',?)""",
                (fact_id, draft.supersedes, utc_now()),
            )

    @staticmethod
    def _retire(db: sqlite3.Connection, current: Fact, new_id: str, valid_to: str) -> None:
        db.execute(
            """UPDATE facts SET status='superseded',superseded_by=?,valid_to=?,updated_at=?
               WHERE id=?""",
            (new_id, valid_to, utc_now(), current.id),
        )

    def set_status(
        self,
        fact_id: str,
        status: FactStatus,
        *,
        valid_to: str | None = None,
    ) -> None:
        with self.connection() as db:
            row = db.execute("SELECT id FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                raise MemorySchemaError(f"unknown fact {fact_id!r}")
            db.execute(
                "UPDATE facts SET status=?,valid_to=COALESCE(?,valid_to),updated_at=? WHERE id=?",
                (status.value, valid_to, utc_now(), fact_id),
            )

    def merge_facts(self, fact_ids: Iterable[str], *, keep: str) -> str:
        """Fold duplicate facts into one, preserving all evidence links."""
        targets = [fact_id for fact_id in dict.fromkeys(fact_ids) if fact_id != keep]
        if not targets:
            return keep
        with self.transaction() as db:
            kept = db.execute("SELECT * FROM facts WHERE id=?", (keep,)).fetchone()
            if kept is None:
                raise MemorySchemaError(f"unknown fact {keep!r}")
            for fact_id in targets:
                row = db.execute("SELECT status FROM facts WHERE id=?", (fact_id,)).fetchone()
                if row is None:
                    raise MemorySchemaError(f"unknown fact {fact_id!r}")
                if str(row["status"]) == FactStatus.DELETED.value:
                    raise MemorySchemaError(f"fact {fact_id!r} was deleted")
                db.execute(
                    """INSERT OR IGNORE INTO fact_sources(
                           fact_id,evidence_id,source_type,quote,run_id,event_seq,created_at
                       )
                       SELECT ?,evidence_id,source_type,quote,run_id,event_seq,created_at
                       FROM fact_sources WHERE fact_id=?""",
                    (keep, fact_id),
                )
                db.execute(
                    """INSERT OR IGNORE INTO fact_relations(from_fact,to_fact,relation,created_at)
                       VALUES(?,?,'merged_into',?)""",
                    (fact_id, keep, utc_now()),
                )
                db.execute(
                    """UPDATE facts SET status='superseded',superseded_by=?,updated_at=?
                       WHERE id=?""",
                    (keep, utc_now(), fact_id),
                )
                self._drop_indexes(db, fact_id)
        return keep

    def delete_fact(self, fact_id: str, *, purge_evidence: bool = True) -> list[str]:
        """Remove a fact and its whole version chain without erasing shared evidence."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                raise MemorySchemaError(f"unknown fact {fact_id!r}")
            key = str(row["dedupe_key"])
            chain = [
                self._fact_from_row(item)
                for item in db.execute(
                    "SELECT * FROM facts WHERE dedupe_key=?", (key,),
                ).fetchall()
            ]
            evidence_ids: set[str] = set()
            for version in chain:
                evidence_ids.update(
                    str(source["evidence_id"])
                    for source in db.execute(
                        "SELECT evidence_id FROM fact_sources WHERE fact_id=?",
                        (version.id,),
                    ).fetchall()
                )
                self._drop_indexes(db, version.id)
                db.execute("DELETE FROM fact_sources WHERE fact_id=?", (version.id,))
                db.execute("DELETE FROM facts WHERE id=?", (version.id,))
                db.execute(
                    "DELETE FROM fact_relations WHERE from_fact=? OR to_fact=?",
                    (version.id, version.id),
                )
                db.execute(
                    """INSERT OR IGNORE INTO forget_tombstones(
                           dedupe_key,content_hash,scope,created_at
                       ) VALUES(?,?,?,?)""",
                    (key, content_hash(version.text), version.scope, utc_now()),
                )
            purged: list[str] = []
            if not purge_evidence:
                return sorted(evidence_ids)
            for evidence_id in evidence_ids:
                still_used = db.execute(
                    "SELECT 1 FROM fact_sources WHERE evidence_id=? LIMIT 1",
                    (evidence_id,),
                ).fetchone()
                if still_used is not None:
                    continue
                db.execute(
                    "UPDATE evidence SET content='',suppressed=1 WHERE id=?",
                    (evidence_id,),
                )
                db.execute("DELETE FROM evidence_fts WHERE evidence_id=?", (evidence_id,))
                purged.append(evidence_id)
        return sorted(purged)

    def _drop_indexes(self, db: sqlite3.Connection, fact_id: str) -> None:
        db.execute("DELETE FROM facts_fts WHERE fact_id=?", (fact_id,))
        db.execute("DELETE FROM fact_vectors WHERE fact_id=?", (fact_id,))

    def _is_tombstoned(
        self,
        db: sqlite3.Connection,
        key: str,
        text_hash: str,
        draft: FactDraft,
        available: dict[str, Evidence],
    ) -> bool:
        """Block only claims that re-derive from sources older than the forget.

        A user who states the fact again creates new evidence, which is newer
        than the tombstone and therefore reaches the store normally.
        """
        row = db.execute(
            """SELECT created_at FROM forget_tombstones
               WHERE dedupe_key=? AND content_hash=?""",
            (key, text_hash),
        ).fetchone()
        if row is None:
            return False
        forgotten_at = str(row["created_at"])
        newest_source = max(
            (available[source_id].created_at or "" for source_id in draft.sources),
            default="",
        )
        return newest_source <= forgotten_at

    def facts_since(self, cursor: int, limit: int) -> list[tuple[int, Fact]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT rowid AS row_id, * FROM facts
                   WHERE rowid>? ORDER BY rowid LIMIT ?""",
                (cursor, limit),
            ).fetchall()
        return [(int(row["row_id"]), self._fact_from_row(row)) for row in rows]

    def recent_by_status(self, status: FactStatus, limit: int) -> list[Fact]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM facts WHERE status=? ORDER BY rowid DESC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    def max_fact_rowid(self) -> int:
        with self.connection() as db:
            row = db.execute("SELECT COALESCE(MAX(rowid),0) AS value FROM facts").fetchone()
        assert row is not None
        return int(row["value"])

    def upsert_vector(self, fact_id: str, model: str, vector: list[float]) -> None:
        dim = len(vector)
        if dim <= 0:
            raise MemorySchemaError("embedding vector is empty")
        blob = struct.pack(f"{dim}f", *vector)
        with self.connection() as db:
            db.execute(
                """INSERT INTO fact_vectors(fact_id,model,dim,vector,created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(fact_id) DO UPDATE SET
                       model=excluded.model,dim=excluded.dim,
                       vector=excluded.vector,created_at=excluded.created_at""",
                (fact_id, model, dim, blob, utc_now()),
            )

    def missing_vectors(self, model: str, limit: int) -> list[Fact]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT facts.* FROM facts
                   LEFT JOIN fact_vectors ON fact_vectors.fact_id=facts.id
                        AND fact_vectors.model=?
                   WHERE facts.status IN ('active','dormant')
                     AND fact_vectors.fact_id IS NULL
                   ORDER BY facts.rowid LIMIT ?""",
                (model, limit),
            ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    # -- retrieval helpers ---------------------------------------------------

    def search_fts(
        self,
        match: str,
        *,
        where: str,
        params: list[Any],
        limit: int,
    ) -> list[tuple[str, float]]:
        sql = (
            "SELECT facts_fts.fact_id AS fact_id, bm25(facts_fts) AS rank "
            "FROM facts_fts JOIN facts ON facts.id=facts_fts.fact_id "
            f"WHERE facts_fts MATCH ? AND {where} "
            "ORDER BY rank LIMIT ?"
        )
        with self.connection() as db:
            rows = db.execute(sql, [match, *params, limit]).fetchall()
        return [(str(row["fact_id"]), float(row["rank"])) for row in rows]

    def vector_candidates(
        self,
        model: str,
        *,
        where: str,
        params: list[Any],
    ) -> list[tuple[Fact, list[float]]]:
        sql = (
            "SELECT facts.*,fact_vectors.dim AS vec_dim,fact_vectors.vector AS vec "
            "FROM facts JOIN fact_vectors ON fact_vectors.fact_id=facts.id "
            f"WHERE fact_vectors.model=? AND {where}"
        )
        with self.connection() as db:
            rows = db.execute(sql, [model, *params]).fetchall()
        result: list[tuple[Fact, list[float]]] = []
        for row in rows:
            dim = int(row["vec_dim"])
            blob = bytes(row["vec"])
            if dim <= 0 or len(blob) != dim * 4:
                continue
            result.append((self._fact_from_row(row), list(struct.unpack(f"{dim}f", blob))))
        return result

    def facts_by_ids(self, fact_ids: Iterable[str]) -> dict[str, Fact]:
        wanted = list(dict.fromkeys(fact_ids))
        if not wanted:
            return {}
        placeholders = ",".join("?" for _ in wanted)
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM facts WHERE id IN ({placeholders})", wanted,
            ).fetchall()
        return {str(row["id"]): self._fact_from_row(row) for row in rows}

    def versions_for(self, fact: Fact) -> list[Fact]:
        """Return every stored version sharing this fact's identity key."""
        with self.connection() as db:
            rows = db.execute(
                """SELECT * FROM facts WHERE dedupe_key=?
                   ORDER BY observed_at, rowid""",
                (fact_dedupe_key(fact),),
            ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    # -- jobs ----------------------------------------------------------------

    def enqueue_job(
        self,
        kind: str,
        idempotency_key: str,
        *,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        job_id = f"job_{uuid4().hex[:20]}"
        now = utc_now()
        with self.connection() as db:
            db.execute(
                """INSERT OR IGNORE INTO memory_jobs(
                       id,kind,idempotency_key,status,run_id,payload,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, kind, idempotency_key, JobStatus.PENDING.value, run_id,
                 _json(payload or {}), now, now),
            )
            row = db.execute(
                "SELECT id FROM memory_jobs WHERE idempotency_key=?", (idempotency_key,),
            ).fetchone()
        assert row is not None
        return str(row["id"])

    def claim_jobs(self, kind: str, limit: int, *, max_attempts: int = 3) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        with self.transaction() as db:
            rows = db.execute(
                """SELECT * FROM memory_jobs
                   WHERE kind=? AND status IN ('pending','failed') AND attempts<?
                   ORDER BY created_at LIMIT ?""",
                (kind, max_attempts, limit),
            ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE memory_jobs SET status='processing',attempts=attempts+1,
                       updated_at=? WHERE id=?""",
                    (utc_now(), row["id"]),
                )
                claimed.append({
                    "id": str(row["id"]),
                    "run_id": row["run_id"],
                    "payload": json.loads(row["payload"]),
                    "attempts": int(row["attempts"]) + 1,
                })
        return claimed

    def complete_job(self, job_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE memory_jobs SET status='completed',last_error=NULL,updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )

    def fail_job(self, job_id: str, error: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE memory_jobs SET status='failed',last_error=?,updated_at=? WHERE id=?",
                (error[:2000], utc_now(), job_id),
            )

    def reset_stale_jobs(self, *, older_than_seconds: int = 900) -> int:
        """Return interrupted jobs to the queue; their input is still durable.

        The age guard keeps a live worker in another process from having its
        claim stolen; a job interrupted by a crash is retried after the lease.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=max(0, older_than_seconds))
        ).isoformat(timespec="microseconds")
        with self.connection() as db:
            cursor = db.execute(
                "UPDATE memory_jobs SET status='pending',updated_at=? "
                "WHERE status='processing' AND updated_at<?",
                (utc_now(), cutoff),
            )
        return int(cursor.rowcount or 0)

    def job_counts(self) -> tuple[int, int]:
        with self.connection() as db:
            pending = db.execute(
                "SELECT COUNT(*) AS value FROM memory_jobs WHERE status IN ('pending','processing')",
            ).fetchone()
            failed = db.execute(
                "SELECT COUNT(*) AS value FROM memory_jobs WHERE status='failed'",
            ).fetchone()
        assert pending is not None and failed is not None
        return int(pending["value"]), int(failed["value"])

    # -- dream state ---------------------------------------------------------

    def create_dream_run(
        self,
        *,
        cursor_from: int,
        cursor_to: int,
        proposal: dict[str, Any] | None = None,
    ) -> str:
        run_id = f"dream_{uuid4().hex[:20]}"
        now = utc_now()
        with self.connection() as db:
            db.execute(
                """INSERT INTO dream_runs(
                       id,status,fact_cursor_from,fact_cursor_to,proposal,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (run_id, "planned", cursor_from, cursor_to,
                 None if proposal is None else _json(proposal), now, now),
            )
        return run_id

    def claim_dream_run(
        self,
        *,
        cursor_from: int,
        cursor_to: int,
    ) -> str | None:
        """Start one consolidation, refusing a second concurrent pass."""
        run_id = f"dream_{uuid4().hex[:20]}"
        now = utc_now()
        with self.transaction() as db:
            active = db.execute(
                "SELECT id FROM dream_runs WHERE status NOT IN ('completed','failed')",
            ).fetchone()
            if active is not None:
                return None
            db.execute(
                """INSERT INTO dream_runs(
                       id,status,fact_cursor_from,fact_cursor_to,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?)""",
                (run_id, "planned", cursor_from, cursor_to, now, now),
            )
        return run_id

    def apply_dream_operations(
        self,
        run_id: str,
        operations: list[dict[str, Any]],
        *,
        changes: int,
    ) -> None:
        """Apply a validated change set and record that it is durably applied."""
        evidence_ids = [
            source for operation in operations
            for source in cast(list[str], operation.get("evidence", []))
        ]
        available = self.evidence(evidence_ids)
        with self.transaction() as db:
            before = {
                str(row["id"]): (str(row["status"]), row["superseded_by"])
                for row in db.execute("SELECT id,status,superseded_by FROM facts").fetchall()
            }
            for operation in operations:
                kind = operation["op"]
                if kind == "add_fact":
                    draft = _draft_from_operation(operation)
                    self._verify_sources(draft, available)
                    key = self._draft_key(draft)
                    if self._is_tombstoned(
                        db, key, content_hash(draft.text), draft, available,
                    ):
                        continue
                    self._apply_draft(db, draft, key, available)
                elif kind == "merge_facts":
                    keep = str(operation["keep"])
                    for fact_id in cast(list[str], operation.get("merge", [])):
                        if fact_id == keep:
                            continue
                        db.execute(
                            """INSERT OR IGNORE INTO fact_sources(
                                   fact_id,evidence_id,source_type,quote,run_id,event_seq,created_at
                               )
                               SELECT ?,evidence_id,source_type,quote,run_id,event_seq,created_at
                               FROM fact_sources WHERE fact_id=?""",
                            (keep, fact_id),
                        )
                        db.execute(
                            """INSERT OR IGNORE INTO fact_relations(
                                   from_fact,to_fact,relation,created_at
                               ) VALUES(?,?,'merged_into',?)""",
                            (fact_id, keep, utc_now()),
                        )
                        db.execute(
                            """UPDATE facts SET status='superseded',superseded_by=?,updated_at=?
                               WHERE id=? AND status!='deleted'""",
                            (keep, utc_now(), fact_id),
                        )
                        self._drop_indexes(db, fact_id)
                elif kind == "set_status":
                    db.execute(
                        "UPDATE facts SET status=?,updated_at=? WHERE id=? AND status!='deleted'",
                        (str(operation["status"]), utc_now(), str(operation["fact_id"])),
                    )
                else:  # validated operations are a closed set
                    raise MemorySchemaError(f"unsupported dream operation {kind!r}")
            after = {
                str(row["id"]): (str(row["status"]), row["superseded_by"])
                for row in db.execute("SELECT id,status,superseded_by FROM facts").fetchall()
            }
            transitions = [
                {
                    "id": fact_id,
                    "before_status": before.get(fact_id, (None, None))[0],
                    "before_superseded_by": before.get(fact_id, (None, None))[1],
                    "after_status": status,
                }
                for fact_id, (status, _superseded) in after.items()
                if before.get(fact_id, (None, None))[0] != status
            ]
            transitions.extend({
                "id": fact_id,
                "before_status": status,
                "before_superseded_by": superseded_by,
                "after_status": None,
            } for fact_id, (status, superseded_by) in before.items() if fact_id not in after)
            db.execute(
                """UPDATE dream_runs SET status='applying',applied=?,updated_at=?
                   WHERE id=?""",
                (
                    _json({
                        "changes": changes,
                        "operations": len(operations),
                        "transitions": transitions,
                    }),
                    utc_now(),
                    run_id,
                ),
            )

    def set_dream_files(self, run_id: str, files: dict[str, str]) -> None:
        """Record the post-consolidation hash of every rewritten core file."""
        record = self.dream_run(run_id)
        if record is None:
            raise MemorySchemaError(f"unknown dream run {run_id!r}")
        applied = record.get("applied") or {}
        applied = dict(applied) if isinstance(applied, dict) else {}
        merged = dict(applied.get("files") or {})
        merged.update(files)
        applied["files"] = merged
        self.update_dream_run(run_id, applied=applied)

    def set_dream_head(self, run_id: str, head: str | None) -> None:
        """Record the Git HEAD seen before this operation writes files."""
        record = self.dream_run(run_id)
        if record is None:
            raise MemorySchemaError(f"unknown dream run {run_id!r}")
        applied = record.get("applied") or {}
        applied = dict(applied) if isinstance(applied, dict) else {}
        applied["head_before"] = head
        self.update_dream_run(run_id, applied=applied)

    def restore_dream_facts(self, transitions: list[dict[str, Any]]) -> int:
        """Undo fact transitions, without resurrecting anything since deleted."""
        restored = 0
        with self.transaction() as db:
            for transition in transitions:
                fact_id = str(transition["id"])
                row = db.execute("SELECT status FROM facts WHERE id=?", (fact_id,)).fetchone()
                before_status = transition.get("before_status")
                after_status = transition.get("after_status")
                if row is not None and after_status and str(row["status"]) != str(after_status):
                    # A later consolidation or correction changed this fact;
                    # restoring the older operation must not clobber it.
                    continue
                if before_status is None:
                    if row is not None:
                        self._drop_indexes(db, fact_id)
                        db.execute("DELETE FROM fact_sources WHERE fact_id=?", (fact_id,))
                        db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
                        restored += 1
                    continue
                if row is None:
                    # The fact was deleted (forgotten) after this consolidation.
                    # Restoring it would expose content the user asked to erase.
                    continue
                db.execute(
                    "UPDATE facts SET status=?,superseded_by=?,valid_to=NULL,updated_at=? WHERE id=?",
                    (
                        str(before_status),
                        transition.get("before_superseded_by"),
                        utc_now(),
                        fact_id,
                    ),
                )
                restored += 1
        return restored

    def record_restore(self, original_run_id: str, changes: int) -> str:
        run_id = f"dream_{uuid4().hex[:20]}"
        now = utc_now()
        with self.connection() as db:
            db.execute(
                """INSERT INTO dream_runs(
                       id,status,fact_cursor_from,fact_cursor_to,applied,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id, "completed", 0, 0,
                    _json({"restore_of": original_run_id, "changes": changes}), now, now,
                ),
            )
        return run_id

    def update_dream_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        proposal: dict[str, Any] | None = None,
        applied: dict[str, Any] | None = None,
        git_commit: str | None = None,
        error: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        assignments = ["updated_at=?"]
        params: list[Any] = [utc_now()]
        if status is not None:
            assignments.append("status=?")
            params.append(status)
        if proposal is not None:
            assignments.append("proposal=?")
            params.append(_json(proposal))
        if applied is not None:
            assignments.append("applied=?")
            params.append(_json(applied))
        if git_commit is not None:
            assignments.append("git_commit=?")
            params.append(git_commit)
        if error is not None:
            assignments.append("error=?")
            params.append(error[:2000])
        if tokens_in is not None:
            assignments.append("tokens_in=?")
            params.append(tokens_in)
        if tokens_out is not None:
            assignments.append("tokens_out=?")
            params.append(tokens_out)
        params.append(run_id)
        with self.connection() as db:
            db.execute(f"UPDATE dream_runs SET {','.join(assignments)} WHERE id=?", params)

    def dream_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM dream_runs WHERE id=?", (run_id,)).fetchone()
        return None if row is None else self._dream_row(row)

    def unfinished_dream_runs(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM dream_runs WHERE status NOT IN ('completed','failed') "
                "ORDER BY created_at",
            ).fetchall()
        return [self._dream_row(row) for row in rows]

    def last_dream_run(self) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM dream_runs ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return None if row is None else self._dream_row(row)

    @staticmethod
    def _dream_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "cursor_from": int(row["fact_cursor_from"]),
            "cursor_to": int(row["fact_cursor_to"]),
            "proposal": json.loads(row["proposal"]) if row["proposal"] else None,
            "applied": json.loads(row["applied"]) if row["applied"] else None,
            "git_commit": row["git_commit"],
            "error": row["error"],
            "tokens_in": int(row["tokens_in"]),
            "tokens_out": int(row["tokens_out"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def dream_versions(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM dream_runs WHERE status='completed' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._dream_row(row) for row in rows]

    def add_skill_proposal(
        self, dream_run_id: str, name: str, description: str, body: str,
    ) -> str:
        proposal_id = f"skillprop_{uuid4().hex[:16]}"
        with self.connection() as db:
            db.execute(
                """INSERT INTO skill_proposals(
                       id,dream_run_id,name,description,body,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (proposal_id, dream_run_id, name, description, body, utc_now()),
            )
        return proposal_id

    def skill_proposals(self, status: str = "pending") -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM skill_proposals WHERE status=? ORDER BY created_at",
                (status,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "dream_run_id": str(row["dream_run_id"]),
                "name": str(row["name"]),
                "description": str(row["description"]),
                "body": str(row["body"]),
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    # -- statistics ----------------------------------------------------------

    def stats(self) -> MemoryStats:
        with self.connection() as db:
            def count(sql: str, params: tuple[Any, ...] = ()) -> int:
                row = db.execute(sql, params).fetchone()
                return 0 if row is None else int(row[0])

            active = count("SELECT COUNT(*) FROM facts WHERE status='active'")
            disputed = count("SELECT COUNT(*) FROM facts WHERE status='disputed'")
            dormant = count("SELECT COUNT(*) FROM facts WHERE status='dormant'")
            evidence_items = count("SELECT COUNT(*) FROM evidence WHERE suppressed=0")
            pending = count(
                "SELECT COUNT(*) FROM memory_jobs WHERE status IN ('pending','processing')"
            )
            failed = count("SELECT COUNT(*) FROM memory_jobs WHERE status='failed'")
            cursor_row = db.execute(
                "SELECT value FROM memory_meta WHERE key='dream_fact_cursor'",
            ).fetchone()
            cursor = int(str(cursor_row["value"])) if cursor_row is not None else 0
            unconsolidated_row = db.execute(
                "SELECT COUNT(*) FROM facts WHERE rowid>?", (cursor,),
            ).fetchone()
            unconsolidated = 0 if unconsolidated_row is None else int(unconsolidated_row[0])
        last = self.last_dream_run()
        applied = last["applied"] if last is not None else None
        return MemoryStats(
            active_facts=active,
            disputed_facts=disputed,
            dormant_facts=dormant,
            evidence_items=evidence_items,
            pending_jobs=pending,
            failed_jobs=failed,
            unconsolidated_facts=unconsolidated,
            last_dream_at=None if last is None else last["updated_at"],
            last_dream_status=None if last is None else last["status"],
            last_dream_error=None if last is None else last["error"],
            last_dream_changes=(
                int(applied.get("changes", 0)) if isinstance(applied, dict) else 0
            ),
            last_dream_tokens=0 if last is None else last["tokens_in"] + last["tokens_out"],
        )

    def unconsolidated_count(self) -> int:
        cursor = int(self.meta_get("dream_fact_cursor") or 0)
        with self.connection() as db:
            row = db.execute("SELECT COUNT(*) FROM facts WHERE rowid>?", (cursor,)).fetchone()
        return 0 if row is None else int(row[0])

    def complete_dream_cursor(self, run_id: str, cursor_to: int) -> None:
        """Mark the consolidation applied and advance the cursor together."""
        with self.transaction() as db:
            db.execute(
                "UPDATE dream_runs SET status='completed',error=NULL,updated_at=? WHERE id=?",
                (utc_now(), run_id),
            )
            db.execute(
                "INSERT INTO memory_meta(key,value) VALUES('dream_fact_cursor',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(cursor_to),),
            )

    # -- row decoding --------------------------------------------------------

    @staticmethod
    def _fact_from_row(row: sqlite3.Row) -> Fact:
        value = json.loads(row["value"])
        if isinstance(value, dict):
            text = str(value.get("text", ""))
            attributes = value.get("attributes")
        else:
            text = str(row["value_text"])
            attributes = None
        return Fact(
            id=str(row["id"]),
            subject=str(row["subject"]),
            predicate=str(row["predicate"]),
            text=text,
            kind=FactKind(str(row["kind"])),
            scope=str(row["scope"]),
            status=FactStatus(str(row["status"])),
            source_type=SourceType(str(row["source_type"])),
            observed_at=row["observed_at"],
            valid_from=str(row["valid_from"]),
            valid_to=row["valid_to"],
            supersedes=row["supersedes"],
            superseded_by=row["superseded_by"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            confidence=row["confidence"],
            attributes=attributes if isinstance(attributes, dict) else {},
        )


def _draft_from_operation(operation: dict[str, Any]) -> FactDraft:
    return FactDraft(
        subject=str(operation["subject"]),
        predicate=str(operation["predicate"]),
        text=str(operation["text"]),
        kind=FactKind(str(operation["kind"])),
        scope=str(operation.get("scope") or "global"),
        sources=tuple(cast(list[str], operation.get("evidence", []))),
        source_type=SourceType(str(operation["source_type"])),
        observed_at=operation.get("observed_at"),
        supersedes=operation.get("supersedes"),
    )


def fact_dedupe_key(fact: Fact) -> str:
    """Public helper for callers that need the key a stored fact used."""
    key = dedupe_key(fact.scope, fact.subject, fact.predicate)
    if single_valued(fact.kind):
        return key
    return f"{key}|{content_hash(normalize_text(fact.text))[:16]}"


__all__ = [
    "FactStore",
    "SCHEMA_VERSION",
    "fact_dedupe_key",
]
