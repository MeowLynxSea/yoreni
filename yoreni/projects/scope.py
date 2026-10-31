"""Conversation scopes: the main chat and one project.

A scope is the identity that Events, Runs, transcripts and project memory are
filed under. It is deliberately a plain string so it can be stored and compared
without a second registry: `global` is the main chat, `project:<id>` is one
project. Memory facts use the same vocabulary, so one value carries through
context assembly, retrieval and deletion.
"""

from __future__ import annotations

import re
from typing import Any

GLOBAL_SCOPE = "global"
PROJECT_SCOPE_PREFIX = "project:"
# Event metadata key carrying the project a WebUI message was sent from.
PROJECT_ID_METADATA_KEY = "project_id"
_PROJECT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class ProjectScopeError(ValueError):
    """Raised when a scope value does not name the main chat or a project ID."""


def project_scope(project_id: str) -> str:
    """Return the scope string for one project ID."""
    if not _PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ProjectScopeError(f"invalid project id {project_id!r}")
    return f"{PROJECT_SCOPE_PREFIX}{project_id}"


def project_id_from_scope(scope: str) -> str | None:
    """Return the project ID a scope names, or None for the main chat."""
    if scope == GLOBAL_SCOPE:
        return None
    if scope.startswith(PROJECT_SCOPE_PREFIX):
        candidate = scope[len(PROJECT_SCOPE_PREFIX):]
        if _PROJECT_ID_PATTERN.fullmatch(candidate):
            return candidate
    raise ProjectScopeError(f"invalid conversation scope {scope!r}")


def normalize_chat_scope(value: Any) -> str:
    """Validate scope input from a boundary that has not been checked yet."""
    if value is None or value == "":
        return GLOBAL_SCOPE
    if not isinstance(value, str):
        raise ProjectScopeError("scope must be a string")
    scope = value.strip()
    project_id_from_scope(scope)
    return scope


def is_project_scope(scope: str) -> bool:
    return project_id_from_scope(scope) is not None
