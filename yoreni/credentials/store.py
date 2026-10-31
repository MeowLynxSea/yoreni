"""Encrypted persistence for connectors, accounts, and authentication requests.

Plaintext secrets never reach this module's schema: account payloads are sealed
by :class:`yoreni.credentials.crypto.SecretBox` before insertion and opened only
by the credential manager at a controlled boundary.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from yoreni.config.paths import get_data_dir
from yoreni.credentials.crypto import SecretBox
from yoreni.credentials.models import (
    Account,
    AccountStatus,
    AuthMethod,
    ConnectorSpec,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connectors (
    id TEXT PRIMARY KEY,
    spec TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL,
    label TEXT NOT NULL,
    auth_method TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL,
    allowed_tools TEXT NOT NULL DEFAULT '[]',
    allowed_domains TEXT NOT NULL DEFAULT '[]',
    restorable INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_requests (
    id TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL,
    account_id TEXT,
    run_id TEXT,
    method TEXT NOT NULL,
    fields TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    allowed_tools TEXT NOT NULL DEFAULT '[]',
    allowed_domains TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class StoredAccount:
    account: Account
    payload: dict[str, str]


def _json_list(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in cast(list[object], parsed))


class CredentialStore:
    """SQLite-backed encrypted credential store."""

    def __init__(self, path: Path | None = None, *, box: SecretBox | None = None) -> None:
        self.path = path or (get_data_dir() / "credentials.sqlite3")
        if box is None:
            from yoreni.credentials.crypto import load_or_create_master_key

            box = SecretBox(load_or_create_master_key())
        self._box = box
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager  # pyright: ignore[reportDeprecated]
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # -- connectors -------------------------------------------------------

    def save_connector(self, spec: ConnectorSpec) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO connectors (id, spec, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET spec = excluded.spec",
                (spec.id, json.dumps(spec.to_public_dict() | {
                    "oauth_authorization_url": spec.oauth_authorization_url,
                    "oauth_token_url": spec.oauth_token_url,
                    "oauth_client_id": spec.oauth_client_id,
                    "oauth_scopes": list(spec.oauth_scopes),
                }), time.time()),
            )

    def list_connectors(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT spec FROM connectors ORDER BY id").fetchall()
        return [cast(dict[str, Any], json.loads(row["spec"])) for row in rows]

    def delete_connector(self, connector_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM connectors WHERE id = ?", (connector_id,))
            return cursor.rowcount > 0

    # -- accounts ---------------------------------------------------------

    def save_account(
        self,
        account: Account,
        payload: dict[str, str],
    ) -> None:
        sealed = self._box.seal(json.dumps(payload, separators=(",", ":")), aad=account.id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO accounts (id, connector_id, label, auth_method, status, payload, "
                "created_at, updated_at, expires_at, allowed_tools, allowed_domains, restorable) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET label=excluded.label, status=excluded.status, "
                "payload=excluded.payload, updated_at=excluded.updated_at, "
                "expires_at=excluded.expires_at, allowed_tools=excluded.allowed_tools, "
                "allowed_domains=excluded.allowed_domains, restorable=excluded.restorable",
                (
                    account.id,
                    account.connector_id,
                    account.label,
                    account.auth_method.value,
                    account.status.value,
                    sealed,
                    account.created_at,
                    account.updated_at,
                    account.expires_at,
                    json.dumps(list(account.allowed_tools)),
                    json.dumps(list(account.allowed_domains)),
                    1 if account.restorable else 0,
                ),
            )

    def _row_to_account(self, row: sqlite3.Row) -> Account:
        return Account(
            id=cast(str, row["id"]),
            connector_id=cast(str, row["connector_id"]),
            label=cast(str, row["label"]),
            auth_method=AuthMethod(cast(str, row["auth_method"])),
            status=AccountStatus(cast(str, row["status"])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=cast(float | None, row["expires_at"]),
            allowed_tools=_json_list(cast(str, row["allowed_tools"])),
            allowed_domains=_json_list(cast(str, row["allowed_domains"])),
            restorable=bool(row["restorable"]),
            secret_fields=tuple(sorted(self._open_payload(row).keys())),
        )

    def _open_payload(self, row: sqlite3.Row) -> dict[str, str]:
        raw = self._box.open(cast(str, row["payload"]), aad=cast(str, row["id"]))
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("credential payload must be a JSON object")
        return {str(key): str(item) for key, item in cast(dict[object, object], value).items()}

    def get_account(self, account_id: str) -> StoredAccount | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return StoredAccount(self._row_to_account(row), self._open_payload(row))

    def list_accounts(self) -> list[StoredAccount]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
        return [StoredAccount(self._row_to_account(row), self._open_payload(row)) for row in rows]

    def delete_account(self, account_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            connection.execute("UPDATE auth_requests SET account_id = NULL WHERE account_id = ?", (account_id,))
            return cursor.rowcount > 0

    def set_account_status(self, account_id: str, status: AccountStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, time.time(), account_id),
            )

    def rewrap(self, new_box: SecretBox) -> int:
        """Re-encrypt every payload with a new master key in one transaction."""
        with self._connect() as connection:
            rows = connection.execute("SELECT id, payload FROM accounts").fetchall()
            rewrapped: list[tuple[str, str]] = []
            for row in rows:
                account_id = cast(str, row["id"])
                plaintext = self._box.open(cast(str, row["payload"]), aad=account_id)
                rewrapped.append((account_id, new_box.seal(plaintext, aad=account_id)))
            connection.executemany(
                "UPDATE accounts SET payload = ? WHERE id = ?",
                [(payload, account_id) for account_id, payload in rewrapped],
            )
        self._box = new_box
        return len(rewrapped)

    # -- authentication requests -----------------------------------------

    def save_auth_request(self, request: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO auth_requests (id, connector_id, account_id, run_id, "
                "method, fields, status, purpose, created_at, expires_at, allowed_tools, "
                "allowed_domains) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request["id"],
                    request["connector_id"],
                    request.get("account_id"),
                    request.get("run_id"),
                    request["method"],
                    json.dumps(list(request.get("fields") or [])),
                    request["status"],
                    request.get("purpose") or "",
                    float(request["created_at"]),
                    float(request["expires_at"]),
                    json.dumps(list(request.get("allowed_tools") or [])),
                    json.dumps(list(request.get("allowed_domains") or [])),
                ),
            )

    def get_auth_request(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_requests WHERE id = ?", (request_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "connector_id": row["connector_id"],
            "account_id": row["account_id"],
            "run_id": row["run_id"],
            "method": row["method"],
            "fields": list(_json_list(row["fields"])),
            "status": row["status"],
            "purpose": row["purpose"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "allowed_tools": list(_json_list(row["allowed_tools"])),
            "allowed_domains": list(_json_list(row["allowed_domains"])),
        }

    def set_auth_request_status(self, request_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE auth_requests SET status = ? WHERE id = ?", (status, request_id),
            )

    def expire_auth_requests(self, now: float | None = None) -> int:
        moment = now if now is not None else time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE auth_requests SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at <= ?",
                (moment,),
            )
            return cursor.rowcount


def new_account_id() -> str:
    return f"acct_{uuid.uuid4().hex[:20]}"


def new_auth_request_id() -> str:
    return f"auth_{uuid.uuid4().hex[:20]}"
