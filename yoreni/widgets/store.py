"""Filesystem storage for dynamic Home widgets."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from loguru import logger

from yoreni.security.workspace_policy import require_path_within
from yoreni.utils.file_io import write_text_atomic
from yoreni.widgets.types import (
    MAX_DATA_BYTES,
    MAX_HTML_BYTES,
    MAX_SCRIPT_BYTES,
    WidgetDefinition,
    WidgetError,
    WidgetNotFoundError,
    dump_json,
    now_ms,
    parse_definition,
    parse_envelope,
    validate_widget_filename,
    validate_widget_id,
)

_MANIFEST = "widget.json"
_VIEW = "view.html"
_DATA = "data.json"


class WidgetStore:
    """One directory per widget under ``root``."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def ids(self) -> list[str]:
        found: list[str] = []
        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            try:
                widget_id = validate_widget_id(entry.name)
                self._dir(widget_id)
            except (WidgetError, OSError) as exc:
                logger.warning("skipping widget entry {}: {}", entry.name, exc)
                continue
            found.append(widget_id)
        return sorted(found)

    def read(self, widget_id: str) -> WidgetDefinition:
        directory = self._dir(widget_id)
        manifest = directory / _MANIFEST
        if not manifest.is_file():
            raise WidgetNotFoundError(f"widget '{widget_id}' not found")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WidgetError(f"widget '{widget_id}' has an unreadable manifest") from exc
        return parse_definition(raw, expected_id=widget_id)

    def read_html(self, widget_id: str) -> str | None:
        path = self._dir(widget_id) / _VIEW
        return self._read_text(path, MAX_HTML_BYTES, "view.html")

    def read_script(self, widget_id: str, filename: str) -> str | None:
        path = self._file(widget_id, filename)
        return self._read_text(path, MAX_SCRIPT_BYTES, filename)

    def read_envelope(self, widget_id: str) -> tuple[object, int, str | None] | None:
        path = self._dir(widget_id) / _DATA
        text = self._read_text(path, MAX_DATA_BYTES, "data.json")
        if text is None:
            return None
        try:
            return parse_envelope(json.loads(text))
        except json.JSONDecodeError:
            return None

    def prepare(self, widget_id: str) -> None:
        """Create the widget directory before files and manifest are written."""
        self._dir(widget_id).mkdir(parents=True, exist_ok=True)

    def write_definition(self, definition: WidgetDefinition) -> None:
        directory = self._dir(definition.id)
        directory.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            directory / _MANIFEST,
            json.dumps(definition.to_manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def write_html(self, widget_id: str, html: str) -> None:
        path = self._dir(widget_id) / _VIEW
        payload = html.encode("utf-8")
        if len(payload) > MAX_HTML_BYTES:
            raise WidgetError(f"html must be at most {MAX_HTML_BYTES // 1024} KB")
        self._require_parent(widget_id)
        write_text_atomic(path, html)

    def write_script(self, widget_id: str, filename: str, content: str) -> None:
        path = self._file(widget_id, filename)
        payload = content.encode("utf-8")
        if len(payload) > MAX_SCRIPT_BYTES:
            raise WidgetError(f"script must be at most {MAX_SCRIPT_BYTES // 1024} KB")
        self._require_parent(widget_id)
        write_text_atomic(path, content)

    def write_data(self, widget_id: str, data: object, *, error: str | None = None) -> None:
        path = self._dir(widget_id) / _DATA
        envelope = {"data": data, "updated_at_ms": now_ms(), "error": error}
        encoded = dump_json(envelope)
        self._require_parent(widget_id)
        write_text_atomic(path, encoded + "\n")

    def remove(self, widget_id: str) -> None:
        directory = self._dir(widget_id)
        if not directory.is_dir():
            raise WidgetNotFoundError(f"widget '{widget_id}' not found")
        trash = Path(tempfile.mkdtemp(prefix=".trash-", dir=self._root))
        try:
            os.replace(directory, trash / widget_id)
        except OSError:
            shutil.rmtree(trash, ignore_errors=True)
            raise
        shutil.rmtree(trash, ignore_errors=True)

    def directory(self, widget_id: str) -> Path:
        """Return the validated directory for one widget."""
        return self._dir(widget_id)

    def _dir(self, widget_id: str) -> Path:
        valid_id = validate_widget_id(widget_id)
        return require_path_within(self._root / valid_id, self._root)

    def _file(self, widget_id: str, filename: str) -> Path:
        valid_name = validate_widget_filename(filename)
        directory = self._dir(widget_id)
        return require_path_within(directory / valid_name, directory)

    def _require_parent(self, widget_id: str) -> None:
        directory = self._dir(widget_id)
        if not directory.is_dir():
            raise WidgetNotFoundError(f"widget '{widget_id}' not found")

    @staticmethod
    def _read_text(path: Path, limit: int, label: str) -> str | None:
        if not path.is_file():
            return None
        try:
            if path.stat().st_size > limit:
                logger.warning("widget {} exceeds {} bytes, ignoring: {}", label, limit, path)
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("failed to read widget {} {}: {}", label, path, exc)
            return None
