"""Durable identity, events and executions of the personal agent.

Conversation projections are deliberately absent from this storage contract.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Literal, TypedDict, cast
from uuid import uuid4

from yoreni.utils.helpers import find_legal_message_start

PERSONAL_CHAT_ID = "yoreni"

RunStatus = Literal["queued", "running", "completed", "failed", "interrupted", "cancelled"]


class RunOutcome(TypedDict):
    content: str
    stop_reason: str


class PendingRun(TypedDict):
    id: str
    status: RunStatus
    error: str | None


class StoredRun(PendingRun):
    event_seq: int
    transcript: list[dict[str, Any]]
    checkpoint: dict[str, Any] | None
    result: RunOutcome | None
    journaled: int
    payload: dict[str, Any]


class WebUIRun(TypedDict):
    id: str
    status: RunStatus
    error: str | None
    event_seq: int
    event_id: str
    source: str
    kind: str
    content: str
    result: RunOutcome | None


class AgentStateConflictError(RuntimeError):
    """A caller tried to replace a state version it has not read."""


@dataclass(frozen=True)
class AgentInput:
    """Trusted application input; transports assign authority, never payloads."""

    source: str
    content: str
    principal: str
    kind: Literal["user", "internal", "external"] = "user"
    event_id: str = field(default_factory=lambda: uuid4().hex)
    channel: str = "cli"
    chat_id: str = "direct"
    ephemeral: bool = False
    media: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentIdentity:
    id: str
    revision: int
    state: dict[str, Any]


class AgentStore:
    """One workspace owns one agent; SQLite transactions arbitrate all writers."""

    def __init__(self, workspace: Path):
        self.path = workspace / "agent.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(source,event_id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
                    status TEXT NOT NULL DEFAULT 'queued',
                    transcript TEXT NOT NULL DEFAULT '[]',
                    checkpoint TEXT,
                    result TEXT,
                    error TEXT,
                    journaled INTEGER NOT NULL DEFAULT 0
                );
            """)
            db.execute("INSERT OR IGNORE INTO agent(singleton,id) VALUES(1,?)", (uuid4().hex,))

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def identity(self) -> AgentIdentity:
        with self.connection() as db:
            row = db.execute("SELECT id,revision,state FROM agent WHERE singleton=1").fetchone()
            assert row is not None
            return AgentIdentity(row["id"], row["revision"], json.loads(row["state"]))

    def update(self, revision: int, state: dict[str, Any]) -> AgentIdentity:
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        with self.connection() as db:
            if db.execute(
                "UPDATE agent SET state=?,revision=revision+1 WHERE singleton=1 AND revision=?",
                (encoded, revision),
            ).rowcount != 1:
                raise AgentStateConflictError("Agent state changed; read it again before updating.")
            row = db.execute("SELECT id FROM agent WHERE singleton=1").fetchone()
            assert row is not None
            return AgentIdentity(row["id"], revision + 1, json.loads(encoded))

    def admit(self, event: AgentInput) -> str:
        if event.principal != "owner":
            raise PermissionError("This event has no access to the personal agent.")
        if event.ephemeral:
            raise ValueError("Ephemeral Runs cannot be admitted to durable storage.")
        payload = json.dumps({
            "source": event.source, "content": event.content, "kind": event.kind,
            "principal": event.principal, "event_id": event.event_id,
            "channel": event.channel, "chat_id": event.chat_id,
            "media": event.media, "metadata": event.metadata, "ephemeral": event.ephemeral,
        }, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO events(source,event_id,payload) VALUES(?,?,?)",
                (event.source, event.event_id, payload),
            )
            row = db.execute(
                "SELECT seq,payload FROM events WHERE source=? AND event_id=?",
                (event.source, event.event_id),
            ).fetchone()
            assert row is not None
            if row["payload"] != payload:
                raise AgentStateConflictError("Event ID was already used for different input.")
            db.execute(
                "INSERT OR IGNORE INTO runs(id,event_seq) VALUES(?,?)", (uuid4().hex, row["seq"]),
            )
            run = db.execute("SELECT id FROM runs WHERE event_seq=?", (row["seq"],)).fetchone()
            assert run is not None
            return str(run["id"])

    def read_run(self, run_id: str) -> StoredRun:
        with self.connection() as db:
            row = db.execute(
                """SELECT runs.*,events.payload FROM runs JOIN events
                   ON events.seq=runs.event_seq WHERE runs.id=?""", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            # These records are produced by this repository's admission and
            # checkpoint transactions; decode the dynamic payloads at this edge.
            return StoredRun(
                id=row["id"], event_seq=row["event_seq"], status=row["status"],
                transcript=json.loads(row["transcript"]),
                checkpoint=json.loads(row["checkpoint"]) if row["checkpoint"] is not None else None,
                result=json.loads(row["result"]) if row["result"] is not None else None,
                error=row["error"], journaled=row["journaled"], payload=json.loads(row["payload"]),
            )

    def find_event(self, source: str, event_id: str) -> StoredRun | None:
        """Find a durable receipt without admitting or executing the Event again."""
        with self.connection() as db:
            row = db.execute(
                """SELECT runs.id FROM runs JOIN events ON events.seq=runs.event_seq
                   WHERE events.source=? AND events.event_id=?""", (source, event_id),
            ).fetchone()
        return self.read_run(row["id"]) if row else None

    def start(self, run_id: str, transcript: list[dict[str, Any]]) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET status='running',transcript=?,error=NULL WHERE id=?",
                (json.dumps(transcript, ensure_ascii=False), run_id),
            )

    def checkpoint(self, run_id: str, payload: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute("UPDATE runs SET checkpoint=? WHERE id=?", (
                json.dumps(payload, ensure_ascii=False), run_id,
            ))

    def finish(
        self, run_id: str, messages: list[dict[str, Any]], result: RunOutcome,
    ) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET status=?,transcript=?,result=?,checkpoint=NULL WHERE id=?",
                ("failed" if result["stop_reason"] in {"error", "tool_error"} else "completed",
                 json.dumps(messages, ensure_ascii=False), json.dumps(result, ensure_ascii=False), run_id),
            )

    def interrupt(self, run_id: str, error: str, *, cancelled: bool = False) -> None:
        with self.connection() as db:
            db.execute("UPDATE runs SET status=?,error=? WHERE id=? AND result IS NULL", (
                "cancelled" if cancelled else "interrupted", error, run_id,
            ))

    def cancel_queued(self, event: AgentInput) -> None:
        with self.connection() as db:
            db.execute(
                """UPDATE runs SET status='cancelled',error='Input cancelled before execution'
                   WHERE status='queued' AND event_seq=(
                     SELECT seq FROM events WHERE source=? AND event_id=?
                   )""", (event.source, event.event_id),
            )

    def pending(self) -> list[PendingRun]:
        with self.connection() as db:
            return [PendingRun(id=row["id"], status=row["status"], error=row["error"]) for row in db.execute(
                """SELECT id,status,error FROM runs
                   WHERE status IN ('queued','running','interrupted')
                   ORDER BY event_seq""",
            )]

    def recent(self, *, limit: int = 8) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT runs.transcript AS messages FROM runs JOIN events ON events.seq=runs.event_seq
                   WHERE runs.result IS NOT NULL
                     AND json_extract(events.payload,'$.kind')='user'
                   ORDER BY events.seq DESC LIMIT ?""", (limit,),
            ).fetchall()
        messages = [message for row in reversed(rows)
                    for message in cast(list[dict[str, Any]], json.loads(row["messages"]))]
        window = messages[-200:]
        return window[find_legal_message_start(window):]

    def webui_history(self, *, limit: int = 80) -> list[WebUIRun]:
        """Return the durable Event/Run projection used by the personal UI.

        The UI needs the original Event separately from the model transcript:
        the latter is model-facing context and may contain runtime annotations
        or tool calls that are not conversational messages.
        """
        with self.connection() as db:
            rows = db.execute(
                """SELECT runs.id,runs.status,runs.error,runs.result,
                          events.seq,events.event_id,events.source,events.payload
                   FROM runs JOIN events ON events.seq=runs.event_seq
                   WHERE json_extract(events.payload,'$.principal')='owner'
                   ORDER BY events.seq DESC LIMIT ?""", (limit,),
            ).fetchall()
        result: list[WebUIRun] = []
        for row in reversed(rows):
            payload = cast(dict[str, Any], json.loads(row["payload"]))
            raw_result = json.loads(row["result"]) if row["result"] is not None else None
            result.append(WebUIRun(
                id=row["id"], status=row["status"], error=row["error"],
                event_seq=row["seq"], event_id=row["event_id"], source=row["source"],
                kind=str(payload.get("kind", "user")), content=str(payload.get("content", "")),
                result=cast(RunOutcome | None, raw_result),
            ))
        return result

    def pending_journal(self) -> list[tuple[str, list[dict[str, Any]]]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT runs.id,runs.transcript AS messages FROM runs
                   JOIN events ON events.seq=runs.event_seq
                   WHERE runs.journaled=0 AND runs.result IS NOT NULL
                     AND json_extract(events.payload,'$.kind')='user'
                   ORDER BY events.seq LIMIT 100""",
            ).fetchall()
        return [(row["id"], json.loads(row["messages"])) for row in rows]

    def mark_journaled(self, run_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE runs SET journaled=1 WHERE id=?", (run_id,))

