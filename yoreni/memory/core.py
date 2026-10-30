"""Core Memory files: always-visible identity, profile and background.

``SOUL.md`` is agent identity and behaviour. Only a trusted operator writes it;
the memory system reads it and enforces behaviour through code, not prose.
``USER.md`` and ``memory/MEMORY.md`` are summaries maintained by consolidation
and always rendered within an explicit token budget.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from yoreni.memory.models import MemorySchemaError, normalize_text
from yoreni.utils.helpers import truncate_text_to_tokens

# Relative to the workspace.
SOUL_PATH = "SOUL.md"
USER_PATH = "USER.md"
MEMORY_PATH = "memory/MEMORY.md"

CORE_WRITABLE = (USER_PATH, MEMORY_PATH)

DEFAULT_CORE_TOKEN_BUDGET = 3_000
# Shares are applied per file and then redistributed from leftovers, so one
# short file never wastes its allocation while another is truncated.
_SHARES = ((SOUL_PATH, 0.4), (USER_PATH, 0.3), (MEMORY_PATH, 0.3))


@dataclass(frozen=True, slots=True)
class CoreSnapshot:
    """Hashes of the core files at one instant."""

    hashes: dict[str, str]

    def matches(self, other: "CoreSnapshot") -> bool:
        return self.hashes == other.hashes


class CoreMemory:
    """Read and atomically maintain the workspace's core memory files."""

    def __init__(self, workspace: Path, *, token_budget: int = DEFAULT_CORE_TOKEN_BUDGET):
        self.workspace = workspace
        self.token_budget = max(400, int(token_budget))

    def path(self, relative: str) -> Path:
        return self.workspace / relative

    def read(self, relative: str) -> str:
        try:
            return self.path(relative).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_soul(self) -> str:
        return self.read(SOUL_PATH)

    def read_user(self) -> str:
        return self.read(USER_PATH)

    def read_memory(self) -> str:
        return self.read(MEMORY_PATH)

    def hash(self, relative: str) -> str:
        try:
            payload = self.path(relative).read_bytes()
        except FileNotFoundError:
            return ""
        return hashlib.sha256(payload).hexdigest()

    def snapshot(self) -> CoreSnapshot:
        return CoreSnapshot({name: self.hash(name) for name in (SOUL_PATH, USER_PATH, MEMORY_PATH)})

    def sections(self) -> list[tuple[str, str]]:
        """Return rendered core sections inside the configured token budget."""
        texts = {
            SOUL_PATH: self.read_soul(),
            USER_PATH: self.read_user(),
            MEMORY_PATH: self.read_memory(),
        }
        budgets = self._allocate(texts)
        sections: list[tuple[str, str]] = []
        for relative, _share in _SHARES:
            text = texts[relative].strip()
            if not text:
                continue
            sections.append((relative, truncate_text_to_tokens(text, budgets[relative])))
        return sections

    def _allocate(self, texts: dict[str, str]) -> dict[str, int]:
        total = self.token_budget
        budgets = {name: max(1, int(total * share)) for name, share in _SHARES}
        # Redistribute tokens a short file does not need. The estimate is
        # conservative (characters >= tokens), so a file is never trimmed to a
        # budget smaller than its content just because the estimate was low.
        leftovers = sum(
            max(0, budgets[name] - _estimate_tokens(texts[name]))
            for name, _share in _SHARES
        )
        hungry = [
            name for name, _share in _SHARES
            if _estimate_tokens(texts[name]) > budgets[name]
        ]
        if hungry and leftovers:
            share = max(1, leftovers // len(hungry))
            for name in hungry:
                budgets[name] += share
        return budgets

    def write(self, relative: str, content: str) -> str:
        """Replace one maintained core file and return its new hash."""
        if relative not in CORE_WRITABLE:
            raise PermissionError(
                f"{relative} is not maintained by the memory system; "
                "only USER.md and memory/MEMORY.md may be rewritten"
            )
        validate_core_content(content)
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        return self.hash(relative)

    def apply(
        self,
        updates: dict[str, str],
        *,
        expected: CoreSnapshot | None = None,
    ) -> CoreSnapshot:
        """Apply a validated change set, refusing to overwrite unseen edits."""
        if expected is not None and not expected.matches(self.snapshot()):
            raise MemorySchemaError(
                "core memory changed since consolidation read it; retry from a fresh snapshot"
            )
        for relative, content in updates.items():
            if relative not in CORE_WRITABLE:
                raise PermissionError(f"{relative} cannot be written by consolidation")
            validate_core_content(content)
        for relative, content in updates.items():
            target = self.path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, content)
        return self.snapshot()

    def scrub(self, needle: str) -> list[str]:
        """Remove lines that restate forgotten content; return changed files."""
        normalized = normalize_text(needle)
        if len(normalized) < 4:
            return []
        changed: list[str] = []
        for relative in CORE_WRITABLE:
            text = self.read(relative)
            if not text:
                continue
            kept = [
                line for line in text.splitlines()
                if normalized not in normalize_text(line)
            ]
            updated = "\n".join(kept).strip()
            if updated == text.strip():
                continue
            _atomic_write(self.path(relative), f"{updated}\n" if updated else "")
            changed.append(relative)
        return changed


def validate_core_content(content: str) -> None:
    if "\x00" in content:
        raise MemorySchemaError("core memory content contains a NUL byte")
    if len(content) > 200_000:
        raise MemorySchemaError("core memory content exceeds 200k characters")


def _estimate_tokens(text: str) -> int:
    """Conservative pre-allocation estimate: characters are never fewer than tokens."""
    return len(text)


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=target.parent, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp_path)
        raise


__all__ = [
    "CORE_WRITABLE",
    "MEMORY_PATH",
    "SOUL_PATH",
    "USER_PATH",
    "CoreMemory",
    "CoreSnapshot",
    "validate_core_content",
]
