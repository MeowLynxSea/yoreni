"""Filesystem locations that hold credential material.

Agent tools must not read or write these paths. They live beside the instance
configuration, never inside the agent workspace.
"""

from __future__ import annotations

from pathlib import Path

from yoreni.config.paths import get_config_path


def credential_state_files() -> tuple[Path, ...]:
    data_dir = get_config_path().expanduser().parent
    return (
        data_dir / "credentials.sqlite3",
        data_dir / "credentials.sqlite3-wal",
        data_dir / "credentials.sqlite3-shm",
        data_dir / "keys" / "credentials.key",
        data_dir / "auth" / "mcp.json",
    )
