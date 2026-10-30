"""Durable identity, events and executions of the personal agent.

Conversation projections are deliberately absent from this storage contract.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Literal, TypedDict, cast
from uuid import uuid4

from yoreni.utils.helpers import find_legal_message_start

# Bumped when the UI projection of a Run changes shape. Rows written before
# message-level persistence expose only their legacy single answer.
PROJECTION_ENTRIES = 1

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


class RunEntry(TypedDict):
    """One durable, user-visible row in a Run's conversation projection."""

    seq: int
    kind: Literal["message", "user", "reaction"]
    payload: dict[str, Any]


class WebUIRun(TypedDict):
    id: str
    status: RunStatus
    error: str | None
    event_seq: int
    event_id: str
    created_at_ms: int
    source: str
    kind: str
    content: str
    result: RunOutcome | None
    activity: list[RunActivity]
    projection: int
    entries: list[RunEntry]


class RunActivity(TypedDict):
    """One in-flight tool call described for the personal UI."""

    name: str
    reason: str


class ConversationRow(TypedDict):
    """One searchable message, keyed by the ID the WebUI renders."""

    id: str
    role: Literal["user", "assistant"]
    text: str
    created_at_ms: int
    event_seq: int


class AgentStateConflictError(RuntimeError):
    """A caller tried to replace a state version it has not read."""


def pending_run_activity(status: str, raw_checkpoint: str | None) -> list[RunActivity]:
    """Describe tool calls an in-flight Run is waiting on.

    The checkpoint is the only durable snapshot of a Run between model calls,
    so a reloaded client can restore the working indicator's description
    without replaying provider state.
    """
    if status not in {"queued", "running"} or not raw_checkpoint:
        return []
    try:
        checkpoint = json.loads(raw_checkpoint)
    except (TypeError, ValueError):
        return []
    if not isinstance(checkpoint, dict):
        return []
    checkpoint_data = cast(dict[str, Any], checkpoint)
    if checkpoint_data.get("phase") != "awaiting_tools":
        return []
    pending_calls = checkpoint_data.get("pending_tool_calls")
    if not isinstance(pending_calls, list):
        return []
    activity: list[RunActivity] = []
    for call in cast(list[Any], pending_calls):
        if not isinstance(call, dict):
            continue
        function = cast(dict[str, Any], call).get("function")
        if not isinstance(function, dict):
            continue
        function_data = cast(dict[str, Any], function)
        name = function_data.get("name")
        raw_arguments = function_data.get("arguments")
        if isinstance(raw_arguments, str):
            try:
                raw_arguments = json.loads(raw_arguments)
            except ValueError:
                raw_arguments = None
        arguments = cast(dict[str, Any], raw_arguments) if isinstance(raw_arguments, dict) else {}
        reason = arguments.get("reason")
        if not isinstance(name, str) or not name or not isinstance(reason, str) or not reason.strip():
            continue
        activity.append(RunActivity(name=name, reason=reason.strip()))
    return activity


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
                    journaled INTEGER NOT NULL DEFAULT 0,
                    projection INTEGER NOT NULL DEFAULT 0,
                    created_at_ms INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS run_entries (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_entries_run
                    ON run_entries(run_id, seq);
            """)
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "projection" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN projection INTEGER NOT NULL DEFAULT 0"
                )
            if "created_at_ms" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN created_at_ms INTEGER NOT NULL DEFAULT 0"
                )
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
                "INSERT OR IGNORE INTO runs(id,event_seq,created_at_ms) VALUES(?,?,?)",
                (uuid4().hex, row["seq"], int(time.time() * 1000)),
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
        """Find a durable receipt without admitting or executing the Event again.

        A message inserted into a running turn never becomes its own Run, so its
        receipt is the owning Run plus the stored conversation row.
        """
        with self.connection() as db:
            row = db.execute(
                """SELECT runs.id FROM runs JOIN events ON events.seq=runs.event_seq
                   WHERE events.source=? AND events.event_id=?""", (source, event_id),
            ).fetchone()
            if row is not None:
                return self.read_run(row["id"])
            inserted = db.execute(
                """SELECT run_entries.run_id,run_entries.payload,runs.status,runs.result
                   FROM run_entries JOIN runs ON runs.id=run_entries.run_id
                   WHERE run_entries.kind='user'
                     AND json_extract(run_entries.payload,'$.id')=?
                   ORDER BY run_entries.seq DESC LIMIT 1""",
                (event_id,),
            ).fetchone()
        if inserted is None:
            return None
        return StoredRun(
            id=str(inserted["run_id"]), event_seq=0, status=cast(RunStatus, inserted["status"]),
            transcript=[], checkpoint=None,
            result=json.loads(inserted["result"]) if inserted["result"] is not None else None,
            error=None, journaled=1,
            payload=cast(dict[str, Any], json.loads(inserted["payload"])),
        )

    def start(self, run_id: str, transcript: list[dict[str, Any]]) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET status='running',transcript=?,error=NULL,projection=? WHERE id=?",
                (json.dumps(transcript, ensure_ascii=False), PROJECTION_ENTRIES, run_id),
            )

    def append_entry(
        self,
        run_id: str,
        kind: Literal["message", "user", "reaction"],
        payload: dict[str, Any],
    ) -> int:
        """Append one durable conversation row without rewriting the transcript.

        Ephemeral Runs have no row to attach to; their rows stay in memory and
        the zero result tells the caller that nothing was persisted.
        """
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        raw_created_at = payload.get("created_at_ms")
        created_at_ms = (
            raw_created_at
            if isinstance(raw_created_at, int) and not isinstance(raw_created_at, bool)
            else int(time.time() * 1000)
        )
        with self.connection() as db:
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None:
                return 0
            cursor = db.execute(
                "INSERT INTO run_entries(run_id,kind,payload,created_at_ms) VALUES(?,?,?,?)",
                (run_id, kind, encoded, created_at_ms),
            )
            return int(cursor.lastrowid or 0)

    def remove_entry(self, run_id: str, entry_id: str) -> None:
        """Drop one conversation row that never reached the model."""
        with self.connection() as db:
            db.execute(
                """DELETE FROM run_entries
                   WHERE run_id=? AND json_extract(payload,'$.id')=?""",
                (run_id, entry_id),
            )

    def _entries_for(self, db: sqlite3.Connection, run_ids: list[str]) -> dict[str, list[RunEntry]]:
        entries: dict[str, list[RunEntry]] = {run_id: [] for run_id in run_ids}
        if not run_ids:
            return entries
        placeholders = ",".join("?" for _ in run_ids)
        rows = db.execute(
            f"""SELECT seq,run_id,kind,payload FROM run_entries
                WHERE run_id IN ({placeholders}) ORDER BY seq""",
            run_ids,
        ).fetchall()
        for row in rows:
            payload = cast(dict[str, Any], json.loads(row["payload"]))
            entries[str(row["run_id"])].append(RunEntry(
                seq=int(row["seq"]),
                kind=cast(Literal["message", "user", "reaction"], row["kind"]),
                payload=payload,
            ))
        return entries

    def recent_user_messages(self, *, limit: int = 6) -> list[dict[str, Any]]:
        """Return recent owner messages, newest first, for reply targeting.

        Prompt Events and messages inserted into a running turn live in different
        tables; both carry a wall-clock timestamp, which is the only ordering
        that survives the split.
        """
        with self.connection() as db:
            prompts = db.execute(
                """SELECT events.event_id AS id,
                          json_extract(events.payload,'$.content') AS content,
                          runs.created_at_ms AS created_at_ms
                   FROM events JOIN runs ON runs.event_seq=events.seq
                   WHERE json_extract(events.payload,'$.kind')='user'
                     AND json_extract(events.payload,'$.principal')='owner'
                   ORDER BY events.seq DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            inserted = db.execute(
                """SELECT json_extract(payload,'$.id') AS id,
                          json_extract(payload,'$.content') AS content,
                          created_at_ms
                   FROM run_entries WHERE kind='user'
                   ORDER BY seq DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        rows = [
            {"id": str(row["id"]), "text": str(row["content"] or ""), "created_at_ms": int(row["created_at_ms"] or 0)}
            for row in (*prompts, *inserted)
        ]
        rows.sort(key=lambda row: row["created_at_ms"], reverse=True)
        return rows[:limit]

    def last_user_chat(self, channel: str) -> str | None:
        """Return the chat the owner most recently used on one channel."""
        with self.connection() as db:
            row = db.execute(
                """SELECT events.payload FROM events
                   WHERE json_extract(events.payload,'$.kind')='user'
                     AND json_extract(events.payload,'$.principal')='owner'
                     AND json_extract(events.payload,'$.channel')=?
                   ORDER BY events.seq DESC LIMIT 1""",
                (channel,),
            ).fetchone()
        if row is None:
            return None
        chat_id = cast(dict[str, Any], json.loads(row["payload"])).get("chat_id")
        return chat_id if isinstance(chat_id, str) and chat_id else None

    def resolve_message_reference(self, message_id: str) -> dict[str, str] | None:
        """Resolve a conversation row to its author and bounded text."""
        with self.connection() as db:
            event = db.execute(
                """SELECT payload FROM events
                   WHERE event_id=? AND json_extract(payload,'$.principal')='owner' LIMIT 1""",
                (message_id,),
            ).fetchone()
            if event is not None:
                payload = cast(dict[str, Any], json.loads(event["payload"]))
                return {
                    "role": "user",
                    "content": str(payload.get("content", ""))[:400],
                }
            entry = db.execute(
                """SELECT kind,payload FROM run_entries
                   WHERE json_extract(payload,'$.id')=? AND kind IN ('message','user')
                   ORDER BY seq DESC LIMIT 1""",
                (message_id,),
            ).fetchone()
        if entry is None:
            return None
        payload = cast(dict[str, Any], json.loads(entry["payload"]))
        return {
            "role": "user" if entry["kind"] == "user" else "assistant",
            "content": str(payload.get("content", ""))[:400],
        }

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

    def conversation_rows(self) -> list[ConversationRow]:
        """Every user-visible message, with the ID the WebUI renders for it.

        Search must reach the whole history rather than the recent window a
        client has loaded, so this projection walks both tables that hold
        conversation rows and adds the answer a pre-projection Run still owns.
        """
        with self.connection() as db:
            prompts = db.execute(
                """SELECT events.event_id AS id,
                          json_extract(events.payload,'$.content') AS content,
                          runs.event_seq,runs.created_at_ms
                   FROM events JOIN runs ON runs.event_seq=events.seq
                   WHERE json_extract(events.payload,'$.kind')='user'
                     AND json_extract(events.payload,'$.principal')='owner'
                   ORDER BY events.seq""",
            ).fetchall()
            entries = db.execute(
                """SELECT json_extract(run_entries.payload,'$.id') AS id,
                          run_entries.kind AS kind,
                          json_extract(run_entries.payload,'$.content') AS content,
                          COALESCE(json_extract(run_entries.payload,'$.created_at_ms'),
                                   run_entries.created_at_ms) AS created_at_ms,
                          runs.event_seq
                   FROM run_entries JOIN runs ON runs.id=run_entries.run_id
                   WHERE run_entries.kind IN ('user','message')
                   ORDER BY run_entries.seq""",
            ).fetchall()
            legacy = db.execute(
                """SELECT id,result,event_seq,created_at_ms FROM runs
                   WHERE projection=0 AND result IS NOT NULL ORDER BY event_seq""",
            ).fetchall()
        rows: list[ConversationRow] = []
        seen: set[str] = set()

        def integer(value: object) -> int:
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        def append(message_id: object, role: Literal["user", "assistant"], content: object,
                   created_at_ms: object, event_seq: object) -> None:
            if not isinstance(message_id, str) or not message_id or message_id in seen:
                return
            if not isinstance(content, str):
                return
            seen.add(message_id)
            rows.append(ConversationRow(
                id=message_id, role=role, text=content,
                created_at_ms=integer(created_at_ms),
                event_seq=integer(event_seq),
            ))

        for row in prompts:
            append(row["id"], "user", row["content"], row["created_at_ms"], row["event_seq"])
        for row in entries:
            append(
                row["id"], "user" if row["kind"] == "user" else "assistant",
                row["content"], row["created_at_ms"], row["event_seq"],
            )
        for row in legacy:
            raw_result = row["result"]
            if raw_result is None:
                continue
            try:
                result = json.loads(raw_result)
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            content = cast(dict[str, Any], result).get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            append(
                f"{row['id']}:answer", "assistant", content,
                row["created_at_ms"], row["event_seq"],
            )
        rows.sort(key=lambda row: (row["created_at_ms"] or row["event_seq"] * 2, row["event_seq"]))
        return rows

    def webui_history(self, *, limit: int = 80, before_seq: int | None = None) -> list[WebUIRun]:
        """Return the durable Event/Run projection used by the personal UI.

        The UI needs the original Event separately from the model transcript:
        the latter is model-facing context and may contain runtime annotations
        or tool calls that are not conversational messages. ``before_seq`` pages
        backwards from a loaded run when a search result lies further back.
        """
        conditions = ["json_extract(events.payload,'$.principal')='owner'"]
        params: list[Any] = []
        if before_seq is not None:
            conditions.append("events.seq < ?")
            params.append(before_seq)
        params.append(limit)
        with self.connection() as db:
            rows = db.execute(
                f"""SELECT runs.id,runs.status,runs.error,runs.result,runs.projection,
                          runs.created_at_ms,
                          CASE WHEN runs.status IN ('queued','running')
                               THEN runs.checkpoint ELSE NULL END AS checkpoint,
                          events.seq,events.event_id,events.source,events.payload
                   FROM runs JOIN events ON events.seq=runs.event_seq
                   WHERE {" AND ".join(conditions)}
                   ORDER BY events.seq DESC LIMIT ?""", params,
            ).fetchall()
            entries = self._entries_for(db, [str(row["id"]) for row in rows])
        result: list[WebUIRun] = []
        for row in reversed(rows):
            payload = cast(dict[str, Any], json.loads(row["payload"]))
            raw_result = json.loads(row["result"]) if row["result"] is not None else None
            result.append(WebUIRun(
                id=row["id"], status=row["status"], error=row["error"],
                event_seq=row["seq"], event_id=row["event_id"], source=row["source"],
                created_at_ms=int(row["created_at_ms"] or 0),
                kind=str(payload.get("kind", "user")), content=str(payload.get("content", "")),
                result=cast(RunOutcome | None, raw_result),
                activity=pending_run_activity(row["status"], row["checkpoint"]),
                projection=int(row["projection"] or 0),
                entries=entries.get(str(row["id"]), []),
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

