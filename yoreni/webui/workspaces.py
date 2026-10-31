"""Persisted WebUI project workspace state."""

from __future__ import annotations

import json
import os
import platform
import socket
import time
from pathlib import Path
from typing import Any, cast

from loguru import logger

from yoreni.config.paths import get_webui_dir
from yoreni.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    WorkspaceScopeError,
    build_workspace_scope,
    default_workspace_scope,
    validate_workspace_scope_payload,
)

WEBUI_WORKSPACE_STATE_SCHEMA_VERSION = 1
_MAX_STATE_FILE_BYTES = 128 * 1024
_WEBUI_SCOPE_CHANNEL = "websocket"
_MAX_DRAFT_SCOPES = 128


def _scope_change_is_non_escalating(current: WorkspaceScope, requested: WorkspaceScope) -> bool:
    """Allow a remote request only when it keeps the project and does not add access."""
    return (
        requested.project_path == current.project_path
        and (not current.restrict_to_workspace or requested.restrict_to_workspace)
    )


def webui_workspace_state_path() -> Path:
    return get_webui_dir() / "workspace-state.json"


def default_webui_workspace_state() -> dict[str, Any]:
    return {
        "schema_version": WEBUI_WORKSPACE_STATE_SCHEMA_VERSION,
        "updated_at": None,
        "recent_projects": [],
        "favorite_projects": [],
    }


def normalize_webui_workspace_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    raw = cast(dict[str, Any], raw)
    state = default_webui_workspace_state()
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    for key, limit in (("recent_projects", 20), ("favorite_projects", 40)):
        paths = raw.get(key)
        if isinstance(paths, list):
            state[key] = list(dict.fromkeys(
                path for path in cast(list[object], paths)
                if isinstance(path, str) and "\0" not in path and Path(path).is_absolute()
            ))[:limit]
    return state


def remember_webui_project(path: Path) -> None:
    state = read_webui_workspace_state()
    recent = [str(path), *(item for item in state["recent_projects"] if item != str(path))][:20]
    if recent != state["recent_projects"]:
        state["recent_projects"] = recent
        try:
            write_webui_workspace_state(state)
        except OSError:
            logger.exception("failed to save recent WebUI projects")


def read_webui_workspace_state() -> dict[str, Any]:
    path = webui_workspace_state_path()
    if not path.is_file():
        return default_webui_workspace_state()
    try:
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("webui workspace state too large, ignoring: {}", path)
            return default_webui_workspace_state()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read webui workspace state failed {}: {}", path, e)
        return default_webui_workspace_state()
    return normalize_webui_workspace_state(raw)


def write_webui_workspace_state(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_webui_workspace_state(raw)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("workspace state is too large")

    path = webui_workspace_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return state
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return state


def workspaces_payload(
    *,
    default_workspace: Path,
    default_restrict_to_workspace: bool,
    can_change_project: bool,
) -> dict[str, Any]:
    state = read_webui_workspace_state()
    default_scope = default_workspace_scope(
        default_workspace,
        default_restrict_to_workspace,
        source_channel=_WEBUI_SCOPE_CHANNEL,
    )
    return {
        "schema_version": WEBUI_WORKSPACE_STATE_SCHEMA_VERSION,
        "default_scope": default_scope.payload(),
        "host": {"name": socket.gethostname(), "platform": platform.system()},
        "favorite_projects": [
            {"name": Path(path).name or path, "path": path}
            for path in state["favorite_projects"]
        ],
        "recent_projects": [
            {"name": Path(path).name or path, "path": path}
            for path in state["recent_projects"]
        ],
        "controls": {
            "can_change_project": can_change_project,
            "can_browse_directories": can_change_project,
            "can_resolve_project": can_change_project,
            "can_manage_favorites": can_change_project,
        },
    }


class WebUIWorkspaceController:
    """Validate the workspace capability carried by each Event."""

    def __init__(self, *, default_workspace: Path, default_restrict_to_workspace: bool):
        self._default_workspace = default_workspace
        self._default_restrict_to_workspace = default_restrict_to_workspace

    def default_scope(self) -> WorkspaceScope:
        return default_workspace_scope(
            self._default_workspace,
            self._default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )


    def restricted_default_scope(self) -> WorkspaceScope:
        """Return the default workspace with access restricted for this request."""
        return build_workspace_scope(
            self._default_workspace,
            "restricted",
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )


    def payload(
        self,
        *,
        can_change_project: bool,
    ) -> dict[str, Any]:
        return workspaces_payload(
            default_workspace=self._default_workspace,
            default_restrict_to_workspace=self._default_restrict_to_workspace,
            can_change_project=can_change_project,
        )


    def resolve_project(self, path: object) -> dict[str, str]:
        """Validate and remember a selection without changing any chat's scope."""
        if not isinstance(path, str) or not path.strip():
            raise WorkspaceScopeError("project_path must be a nonempty string")
        scope = validate_workspace_scope_payload(
            {"project_path": path.strip(), "access_mode": "restricted"},
            default_workspace=self._default_workspace,
            default_restrict_to_workspace=self._default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )
        remember_webui_project(scope.project_path)
        return {"path": str(scope.project_path), "name": scope.project_name}


    def set_favorite_project(self, path: object, pinned: object) -> list[dict[str, str]]:
        """Pin validated folders; allow removing a favorite after it is deleted."""
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise WorkspaceScopeError("path must be a nonempty directory path")
        if not isinstance(pinned, bool):
            raise WorkspaceScopeError("pinned must be a boolean")
        path = path.strip()
        if pinned:
            scope = validate_workspace_scope_payload(
                {"project_path": path, "access_mode": "restricted"},
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._default_restrict_to_workspace,
                source_channel=_WEBUI_SCOPE_CHANNEL,
            )
            path = str(scope.project_path)
        state = read_webui_workspace_state()
        favorites: list[str] = state["favorite_projects"]
        existing = next((item for item in favorites if os.path.normcase(item) == os.path.normcase(path)), None)
        if pinned and existing is None:
            if len(favorites) >= 40:
                raise WorkspaceScopeError("at most 40 favorite folders can be pinned")
            favorites.append(path)
            write_webui_workspace_state(state)
        elif not pinned and existing is not None:
            favorites.remove(existing)
            write_webui_workspace_state(state)
        return [{"name": Path(item).name or item, "path": item} for item in favorites]


    async def scope_from_envelope(self, envelope: dict[str, Any], *, can_change_project: bool) -> WorkspaceScope:
        current = self.default_scope()
        raw = envelope.get(WORKSPACE_SCOPE_METADATA_KEY)
        scope = current if raw is None else validate_workspace_scope_payload(
            raw, default_workspace=self._default_workspace,
            default_restrict_to_workspace=self._default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )
        if not can_change_project and not _scope_change_is_non_escalating(current, scope):
            raise WorkspaceScopeError("project selection is unavailable for this connection", status=403)
        return scope
