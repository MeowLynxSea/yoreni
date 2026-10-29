"""Track file reads whose original results may still be in model context."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _FileReadContext:
    call_id: str
    tool_results: Callable[[], Mapping[str, str]]


_current_file_read: ContextVar[_FileReadContext | None] = ContextVar(
    "yoreni_file_read_context", default=None,
)


@contextmanager
def file_read_context(
    call_id: str, tool_results: Callable[[], Mapping[str, str]],
) -> Generator[None]:
    """Bind one file read; resolve visible results only when checking a prior read."""
    token = _current_file_read.set(_FileReadContext(call_id, tool_results))
    try:
        yield
    finally:
        _current_file_read.reset(token)


@dataclass(slots=True)
class ReadState:
    offset: int
    limit: int | None
    content_hash: str | None
    call_id: str | None
    result_hash: str | None


def _hash_file(p: str) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


class FileStates:
    """Cache read receipts per Run; deduplicate only while their results are visible."""

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state: dict[str, ReadState] = {}

    def record_read(
        self, path: str | Path, offset: int = 1, limit: int | None = None, *,
        content_hash: str | None = None, result: str | None = None,
    ) -> None:
        """Record the file snapshot and complete result of a successful text read."""
        p = str(Path(path).resolve())
        context = _current_file_read.get()
        self._state[p] = ReadState(
            offset=offset,
            limit=limit,
            content_hash=content_hash if content_hash is not None else _hash_file(p),
            call_id=context.call_id if context is not None else None,
            result_hash=hashlib.sha256(result.encode("utf-8")).hexdigest() if result else None,
        )

    def record_write(self, path: str | Path) -> None:
        """Invalidate a prior read after a write; a write summary is not file content."""
        self._state.pop(str(Path(path).resolve()), None)

    def is_unchanged(
        self, path: str | Path, offset: int = 1, limit: int | None = None, *,
        content_hash: str | None = None,
    ) -> bool:
        """Check both file identity and the original result in the actual model input."""
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        context = _current_file_read.get()
        if entry is None or context is None or not entry.call_id or not entry.result_hash:
            return False
        if entry.offset != offset or entry.limit != limit:
            return False
        result = context.tool_results().get(entry.call_id)
        if result is None or hashlib.sha256(result.encode("utf-8")).hexdigest() != entry.result_hash:
            self._state.pop(p, None)
            return False
        current_hash = content_hash if content_hash is not None else _hash_file(p)
        return current_hash is not None and current_hash == entry.content_hash

    def get(self, path: str | Path) -> ReadState | None:
        """Return the raw ReadState entry for a path, or None."""
        return self._state.get(str(Path(path).resolve()))

    def clear(self) -> None:
        """Clear all tracked state (useful for testing)."""
        self._state.clear()




_current_file_states: ContextVar[FileStates | None] = ContextVar(
    "yoreni_file_states",
    default=None,
)


def current_file_states(default: FileStates) -> FileStates:
    """Return the FileStates bound to the current agent task, or a fallback."""
    return _current_file_states.get() or default


def bind_file_states(file_states: FileStates) -> Token[FileStates | None]:
    """Bind file read/write state for the current async task."""
    return _current_file_states.set(file_states)


def reset_file_states(token: Token[FileStates | None]) -> None:
    _current_file_states.reset(token)
