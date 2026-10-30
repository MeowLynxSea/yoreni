"""One-time import of the pre-fact memory journal.

The migration is explicitly one-shot. It backs up every legacy file, imports
the journal as episodic evidence with its original provenance, verifies that
nothing was dropped, and then removes the legacy files so no runtime path can
read them again. Any inconsistency aborts startup.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from yoreni.memory.models import (
    Evidence,
    EvidenceKind,
    MemoryMigrationError,
    SourceType,
    TrustLevel,
    content_hash,
    normalize_timestamp,
    utc_now,
)

if TYPE_CHECKING:
    from yoreni.memory.service import MemoryService

MIGRATION_KEY = "legacy_migration"
JOURNAL_NAME = "history.jsonl"
BACKUP_DIR = "backups"
_LEGACY_FILES = (
    "history.jsonl",
    ".dream_cursor",
    ".cursor",
    "HISTORY.md",
    "MEMORY.md",
)


def migrate_legacy_memory(service: "MemoryService") -> None:
    """Run the legacy import at most once for this workspace."""
    store = service.store
    if store.meta_get(MIGRATION_KEY) == "completed":
        return
    memory_dir = service.workspace / "memory"
    journal = memory_dir / JOURNAL_NAME
    history_path = memory_dir / "HISTORY.md"
    has_journal = journal.exists() and journal.stat().st_size > 0
    has_history = history_path.exists() and history_path.stat().st_size > 0
    if not has_journal and not has_history:
        store.meta_set(MIGRATION_KEY, "completed")
        store.meta_set("migrated_at", utc_now())
        return

    try:
        entries = _read_journal(journal) if has_journal else []
        backup_dir = _backup(service.workspace, entries)
        evidence = _build_evidence(entries)
        history = _history_evidence(memory_dir)
        if history is not None:
            evidence.append(history)
        inserted = store.insert_evidence(evidence)
        _verify(store, evidence)
        _archive_legacy(memory_dir)
    except MemoryMigrationError:
        raise
    except Exception as exc:  # noqa: BLE001 - startup must stop with context
        raise MemoryMigrationError(f"legacy memory migration failed: {exc}") from exc
    store.meta_set(MIGRATION_KEY, "completed")
    store.meta_set("migrated_at", utc_now())
    store.meta_set("legacy_entries", str(len(entries)))
    store.meta_set("legacy_evidence_inserted", str(inserted))
    store.meta_set("legacy_history_imported", "1" if history is not None else "0")
    store.meta_set("legacy_backup", str(backup_dir))
    logger.info(
        "Imported {} legacy journal entries into episodic memory; backup at {}",
        inserted, backup_dir,
    )


def _read_journal(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise MemoryMigrationError(
                    f"{path} line {number} is not valid JSON: {exc.msg}"
                ) from exc
            if not isinstance(parsed, dict):
                raise MemoryMigrationError(f"{path} line {number} is not a JSON object")
            record = cast(dict[str, Any], parsed)
            if not isinstance(record.get("content"), str):
                raise MemoryMigrationError(
                    f"{path} line {number} has no string content"
                )
            entries.append(record)
    return entries


def _backup(workspace: Path, entries: list[dict[str, Any]]) -> Path:
    memory_dir = workspace / "memory"
    stamp = utc_now().replace(":", "-")
    backup_dir = memory_dir / BACKUP_DIR / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    copied: dict[str, str] = {}
    for name in _LEGACY_FILES:
        source = memory_dir / name
        if not source.exists():
            continue
        target = backup_dir / name
        shutil.copy2(source, target)
        copied[name] = _digest(source)
        if _digest(target) != copied[name]:
            raise MemoryMigrationError(f"backup verification failed for {name}")
    for name in ("SOUL.md", "USER.md"):
        source = workspace / name
        if not source.exists():
            continue
        target = backup_dir / name
        shutil.copy2(source, target)
        digest = _digest(source)
        if _digest(target) != digest:
            raise MemoryMigrationError(f"backup verification failed for {name}")
        copied[name] = digest
    manifest = {
        "created_at": utc_now(),
        "source_files": copied,
        "journal_entries": len(entries),
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return backup_dir


def _build_evidence(entries: list[dict[str, Any]]) -> list[Evidence]:
    created = utc_now()
    evidence: list[Evidence] = []
    for index, record in enumerate(entries, start=1):
        content = str(record.get("content", "")).strip()
        if not content:
            continue
        cursor = record.get("cursor")
        stable = str(record.get("entry_id") or cursor or index)
        run_id = record.get("run_id") if isinstance(record.get("run_id"), str) else None
        evidence.append(Evidence(
            id="legacy_" + content_hash(f"{stable}|{content_hash(content)}")[:24],
            run_id=run_id or f"legacy:{stable}",
            event_seq=None,
            kind=EvidenceKind.LEGACY_ENTRY,
            role="user",
            content=content,
            content_hash=content_hash(content),
            source_type=SourceType.LEGACY_IMPORT,
            trust=TrustLevel.ASSERTED,
            observed_at=normalize_timestamp(record.get("timestamp")),
            created_at=created,
        ))
    return evidence


def _history_evidence(memory_dir: Path) -> Evidence | None:
    """Import an old Dream summary as unverified history, never as a fact."""
    path = memory_dir / "HISTORY.md"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return None
    return Evidence(
        id="legacy_history_" + content_hash(content)[:24],
        run_id="legacy:history",
        event_seq=None,
        kind=EvidenceKind.LEGACY_ENTRY,
        role="assistant",
        content=content,
        content_hash=content_hash(content),
        source_type=SourceType.LEGACY_IMPORT,
        trust=TrustLevel.INFERRED,
        observed_at=None,
        created_at=utc_now(),
    )


def _verify(store: Any, evidence: list[Evidence]) -> None:
    found = store.evidence(item.id for item in evidence)
    missing = [item.id for item in evidence if item.id not in found]
    if missing:
        raise MemoryMigrationError(
            f"migration verification failed: {len(missing)} of {len(evidence)} entries missing"
        )
    corrupt = [
        item.id for item in evidence
        if found[item.id].content_hash != item.content_hash
        or found[item.id].content != item.content
    ]
    if corrupt:
        raise MemoryMigrationError(
            f"migration verification failed: {len(corrupt)} entries changed content"
        )


def _archive_legacy(memory_dir: Path) -> None:
    for name in (JOURNAL_NAME, ".dream_cursor", ".cursor"):
        source = memory_dir / name
        if source.exists():
            source.replace(source.with_name(f"{name}.migrated"))
    legacy_history = memory_dir / "HISTORY.md"
    if legacy_history.exists():
        legacy_history.replace(legacy_history.with_name("HISTORY.md.migrated"))


def _digest(path: Path) -> str:
    return content_hash(path.read_text(encoding="utf-8", errors="replace"))


__all__ = ["MIGRATION_KEY", "migrate_legacy_memory"]
