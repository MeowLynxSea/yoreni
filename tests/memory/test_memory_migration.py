"""One-shot migration of the legacy journal into episodic evidence."""

from __future__ import annotations

import json

import pytest
from memory_helpers import MemoryHarness

from yoreni.config.schema import MemoryConfig
from yoreni.memory.models import EvidenceKind, MemoryMigrationError, SourceType, TrustLevel
from yoreni.memory.service import MemoryService


def _journal(tmp_path, *entries: dict) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(exist_ok=True)
    lines = [json.dumps(entry, ensure_ascii=False) for entry in entries]
    (memory_dir / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _service(tmp_path) -> MemoryService:
    return MemoryService(tmp_path, MemoryConfig())


def test_missing_journal_marks_the_migration_complete(tmp_path):
    service = _service(tmp_path)

    assert service.store.meta_get("legacy_migration") == "completed"
    assert service.store.meta_get("legacy_entries") is None
    assert not (tmp_path / "memory" / "backups").exists()


def test_journal_entries_become_episodic_evidence_with_their_provenance(tmp_path):
    _journal(
        tmp_path,
        {"cursor": 3, "timestamp": "2026-03-01T10:00:00+08:00", "content": "我偏好中文回复。"},
        {"cursor": 4, "content": "The user runs macOS."},
        {"cursor": 5, "timestamp": "not a date", "content": "Unknown-time note."},
    )

    service = _service(tmp_path)

    evidence = service.store.evidence_for_run("legacy:3")
    [item] = evidence
    assert item.kind is EvidenceKind.LEGACY_ENTRY
    assert item.source_type is SourceType.LEGACY_IMPORT
    assert item.trust is TrustLevel.ASSERTED
    assert item.content == "我偏好中文回复。"
    assert item.observed_at == "2026-03-01T02:00:00+00:00"

    unknown_time = service.store.evidence_for_run("legacy:5")[0]
    assert unknown_time.observed_at is None
    assert service.store.meta_get("legacy_entries") == "3"
    assert service.store.meta_get("legacy_evidence_inserted") == "3"


def test_blank_lines_are_ignored_but_entries_are_not(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "history.jsonl").write_text(
        '{"cursor": 1, "content": "first"}\n\n{"cursor": 2, "content": "second"}\n',
        encoding="utf-8",
    )

    service = _service(tmp_path)

    assert service.store.meta_get("legacy_entries") == "2"


def test_migration_backs_up_and_archives_every_legacy_file(tmp_path):
    _journal(tmp_path, {"cursor": 1, "content": "remember me"})
    (tmp_path / "memory" / ".dream_cursor").write_text("1", encoding="utf-8")
    (tmp_path / "memory" / "HISTORY.md").write_text("# old history\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("legacy profile\n", encoding="utf-8")

    service = _service(tmp_path)

    backups = list((tmp_path / "memory" / "backups").iterdir())
    assert len(backups) == 1
    manifest = json.loads((backups[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["journal_entries"] == 1
    assert {"history.jsonl", ".dream_cursor", "USER.md"} <= set(manifest["source_files"])
    assert (backups[0] / "history.jsonl").read_text(encoding="utf-8").strip()
    assert service.store.meta_get("legacy_backup") == str(backups[0])

    assert (tmp_path / "memory" / "history.jsonl.migrated").exists()
    assert (tmp_path / "memory" / ".dream_cursor.migrated").exists()
    assert (tmp_path / "memory" / "HISTORY.md.migrated").exists()
    assert not (tmp_path / "memory" / "history.jsonl").exists()


def test_migration_runs_at_most_once(tmp_path):
    _journal(tmp_path, {"cursor": 1, "content": "once"})
    first = _service(tmp_path)
    first_evidence = first.store.evidence_for_run("legacy:1")
    assert len(first_evidence) == 1

    second = _service(tmp_path)

    assert len(second.store.evidence_for_run("legacy:1")) == 1
    assert len(list((tmp_path / "memory" / "backups").iterdir())) == 1


def test_malformed_journal_aborts_startup_without_archiving(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "history.jsonl").write_text(
        '{"cursor": 1, "content": "ok"}\nnot json\n', encoding="utf-8",
    )

    with pytest.raises(MemoryMigrationError, match="not valid JSON"):
        _service(tmp_path)

    assert (memory_dir / "history.jsonl").exists()
    assert not (memory_dir / "history.jsonl.migrated").exists()
    assert not (memory_dir / "backups").exists()


def test_entries_without_content_abort_the_migration(tmp_path):
    _journal(tmp_path, {"cursor": 1})

    with pytest.raises(MemoryMigrationError, match="no string content"):
        _service(tmp_path)


def test_a_failed_verification_prevents_the_completed_mark(tmp_path, monkeypatch):
    _journal(tmp_path, {"cursor": 1, "content": "keep"})

    from yoreni.memory import migration

    original = migration._verify

    def fail_verify(store, evidence):
        raise MemoryMigrationError("verification failed: forced")

    monkeypatch.setattr(migration, "_verify", fail_verify)
    with pytest.raises(MemoryMigrationError, match="verification failed"):
        _service(tmp_path)

    # The journal must still be readable for a retry.
    assert (tmp_path / "memory" / "history.jsonl").exists()
    monkeypatch.setattr(migration, "_verify", original)

    retried = _service(tmp_path)
    assert retried.store.meta_get("legacy_migration") == "completed"
    assert len(retried.store.evidence_for_run("legacy:1")) == 1


def test_migration_does_not_invent_facts(tmp_path):
    _journal(tmp_path, {"cursor": 1, "content": "I like tea."})
    service = _service(tmp_path)

    assert service.store.stats().active_facts == 0
    assert MemoryHarness(service, tmp_path).service.store.stats().evidence_items == 1


def test_old_dream_summary_is_imported_as_unverified_history(tmp_path):
    _journal(tmp_path, {"cursor": 1, "content": "primary"})
    (tmp_path / "memory" / "HISTORY.md").write_text(
        "# Old summary\nThe user was working on the memory rewrite.\n", encoding="utf-8",
    )

    service = _service(tmp_path)

    [summary] = service.store.evidence_for_run("legacy:history")
    assert summary.trust.value == "inferred"
    assert summary.source_type.value == "legacy_import"
    assert summary.role == "assistant"
    assert "memory rewrite" in summary.content
    assert service.store.meta_get("legacy_history_imported") == "1"
    assert service.store.stats().active_facts == 0


def test_history_only_workspace_still_migrates(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "HISTORY.md").write_text("# summary\n", encoding="utf-8")

    service = _service(tmp_path)

    assert service.store.evidence_for_run("legacy:history")
    assert service.store.meta_get("legacy_migration") == "completed"


def test_workspace_setup_no_longer_creates_a_legacy_journal(tmp_path):
    from yoreni.utils.helpers import sync_workspace_templates

    sync_workspace_templates(tmp_path, silent=True)

    assert not (tmp_path / "memory" / "history.jsonl").exists()
    assert not (tmp_path / "memory" / ".dream_cursor").exists()
    assert (tmp_path / "memory" / "MEMORY.md").exists()
