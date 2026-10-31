"""Dynamic Home widget definitions, limits, and boundary validation."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

WIDGET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
WIDGET_FILE_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
RESERVED_WIDGET_IDS = frozenset({"goal", "automations"})

MAX_WIDGETS = 24
MAX_TITLE = 60
MAX_DESCRIPTION = 120
MAX_HTML_BYTES = 128 * 1024
MAX_DATA_BYTES = 256 * 1024
MAX_STDOUT_BYTES = 1024 * 1024
MAX_SCRIPT_BYTES = 256 * 1024
MAX_ARGV = 8
MAX_ARG_LEN = 200
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 30
MIN_REFRESH_SECONDS = 30
MAX_REFRESH_SECONDS = 24 * 60 * 60
MAX_LIST_ITEMS = 20
MAX_TABLE_COLUMNS = 6
MAX_TABLE_ROWS = 20
MAX_TEXT_CHARS = 4000
MAX_TILE_ITEMS = 6
MAX_FACT_ITEMS = 8
MAX_CHART_POINTS = 12
MAX_TIMELINE_ITEMS = 8
MAX_BADGE_ITEMS = 8
MAX_LINK_ITEMS = 6
MAX_URL_CHARS = 2048
MAX_PANEL_BLOCKS = 8

_BLOCK_KINDS: frozenset[str] = frozenset(
    {
        "text",
        "list",
        "table",
        "metrics",
        "progress",
        "facts",
        "chart",
        "timeline",
        "badges",
        "links",
    }
)
_PANEL_BLOCK_KINDS: frozenset[str] = _BLOCK_KINDS | {"divider"}
_VIEW_KINDS: frozenset[str] = _BLOCK_KINDS | {"stat", "panel", "html"}
_TONES: frozenset[str] = frozenset({"neutral", "positive", "warning", "critical"})

ViewKind = Literal[
    "stat",
    "list",
    "table",
    "text",
    "metrics",
    "progress",
    "facts",
    "chart",
    "timeline",
    "badges",
    "links",
    "panel",
    "html",
]


class WidgetError(ValueError):
    """A user- or model-facing widget validation or storage error."""


class WidgetNotFoundError(WidgetError):
    """The requested widget does not exist."""


@dataclass(frozen=True)
class WidgetDefinition:
    id: str
    title: str
    description: str
    view: ViewKind
    command: tuple[str, ...] | None
    timeout_seconds: int
    every_seconds: int | None
    workspace: str | None
    created_at: str
    updated_at: str

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "view": self.view,
            "command": list(self.command) if self.command else None,
            "timeout_seconds": self.timeout_seconds,
            "every_seconds": self.every_seconds,
            "workspace": self.workspace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WidgetSummary:
    definition: WidgetDefinition
    updated_at_ms: int | None
    error: str | None
    has_data: bool
    next_run_at_ms: int | None


@dataclass(frozen=True)
class WidgetSnapshot:
    summary: WidgetSummary
    data: Any
    html: str | None


@dataclass(frozen=True)
class WidgetSpec:
    id: str
    title: str
    description: str = ""
    view: ViewKind = "text"
    html: str | None = None
    script: str | None = None
    script_filename: str = "data.py"
    command: tuple[str, ...] | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    every_seconds: int | None = None
    workspace: str | None = None


@dataclass(frozen=True)
class WidgetPatch:
    title: str | None = None
    description: str | None = None
    view: ViewKind | None = None
    html: str | None = None
    script: str | None = None
    script_filename: str | None = None
    command: tuple[str, ...] | None = None
    timeout_seconds: int | None = None
    every_seconds: int | None = None


@dataclass(frozen=True)
class WidgetRunResult:
    data: Any = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class WidgetChange:
    widget_id: str
    kind: Literal["created", "updated", "deleted", "data"]


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate_widget_id(value: object) -> str:
    if not isinstance(value, str) or WIDGET_ID_RE.fullmatch(value) is None:
        raise WidgetError("widget ids must be 1-64 letters, digits, '_' or '-'")
    if value in RESERVED_WIDGET_IDS:
        raise WidgetError(f"'{value}' is a built-in widget id")
    return value


def validate_widget_filename(value: object) -> str:
    if not isinstance(value, str) or WIDGET_FILE_RE.fullmatch(value) is None:
        raise WidgetError("script filenames must be 1-64 letters, digits, '.', '_' or '-'")
    if value in {".", ".."} or ".." in value:
        raise WidgetError("script filenames must not contain '..'")
    return value


def validate_title(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetError("title must be a non-empty string")
    title = value.strip()
    if len(title) > MAX_TITLE:
        raise WidgetError(f"title must be at most {MAX_TITLE} characters")
    return title


def validate_description(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise WidgetError("description must be a string")
    description = value.strip()
    if len(description) > MAX_DESCRIPTION:
        raise WidgetError(f"description must be at most {MAX_DESCRIPTION} characters")
    return description


def validate_view(value: object) -> ViewKind:
    if not isinstance(value, str) or value not in _VIEW_KINDS:
        raise WidgetError("view must be one of: " + ", ".join(sorted(_VIEW_KINDS)))
    return cast(ViewKind, value)


def validate_command(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, list):
        argv = tuple(cast(list[object], value))
    elif isinstance(value, tuple):
        argv = cast(tuple[object, ...], value)
    else:
        raise WidgetError("command must be a list of strings")
    if not argv:
        return None
    if len(argv) > MAX_ARGV:
        raise WidgetError(f"command accepts at most {MAX_ARGV} arguments")
    for arg in argv:
        if not isinstance(arg, str) or not arg:
            raise WidgetError("command arguments must be non-empty strings")
        if len(arg) > MAX_ARG_LEN:
            raise WidgetError(f"command arguments must be at most {MAX_ARG_LEN} characters")
        if "\x00" in arg or "\n" in arg or "\r" in arg:
            raise WidgetError("command arguments must not contain control characters")
    return cast(tuple[str, ...], argv)


def validate_timeout(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise WidgetError("timeout_seconds must be an integer")
    if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise WidgetError(
            f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
        )
    return value


def validate_refresh_interval(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise WidgetError("every_seconds must be an integer")
    if value == 0:
        return None
    if not MIN_REFRESH_SECONDS <= value <= MAX_REFRESH_SECONDS:
        raise WidgetError(
            f"every_seconds must be 0 or between {MIN_REFRESH_SECONDS} and {MAX_REFRESH_SECONDS}"
        )
    return value


def validate_html(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetError("html must be a non-empty string for view='html'")
    if len(value.encode("utf-8")) > MAX_HTML_BYTES:
        raise WidgetError(f"html must be at most {MAX_HTML_BYTES // 1024} KB")
    return value


def validate_script(value: object) -> str:
    if not isinstance(value, str):
        raise WidgetError("script must be a string")
    if len(value.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise WidgetError(f"script must be at most {MAX_SCRIPT_BYTES // 1024} KB")
    return value


def validate_data(view: ViewKind, value: object) -> object:
    """Validate one view's data contract and reject non-JSON values."""
    if not isinstance(value, dict):
        raise WidgetError("data must be a JSON object")
    data = cast(dict[str, Any], value)
    _validate_json_size(data)
    if view == "html":
        return data
    if view == "stat":
        _require_type(data, "value", (str, int, float))
        for key in ("label", "detail"):
            _optional_string(data, key)
        return data
    if view == "panel":
        _validate_panel(data)
    else:
        _validate_block(view, data)
    return data


def _validate_block(kind: str, data: dict[str, Any]) -> None:
    if kind == "text":
        _validate_text(data)
    elif kind == "list":
        _validate_list(data)
    elif kind == "table":
        _validate_table(data)
    elif kind == "metrics":
        _validate_metrics(data)
    elif kind == "progress":
        _validate_progress(data)
    elif kind == "facts":
        _validate_facts(data)
    elif kind == "chart":
        _validate_chart(data)
    elif kind == "timeline":
        _validate_timeline(data)
    elif kind == "badges":
        _validate_badges(data)
    elif kind == "links":
        _validate_links(data)
    else:
        raise WidgetError(f"unsupported view '{kind}'")


def _validate_list(data: dict[str, Any]) -> None:
    items = data.get("items")
    if not isinstance(items, list):
        raise WidgetError("list data must be {\"items\": [...]}")
    entries = cast(list[object], items)
    if len(entries) > MAX_LIST_ITEMS:
        raise WidgetError(f"list data accepts at most {MAX_LIST_ITEMS} items")
    for entry in entries:
        if not isinstance(entry, dict):
            raise WidgetError("each list item must be an object")
        item = cast(dict[str, Any], entry)
        _require_type(item, "title", (str,))
        for key in ("detail", "meta"):
            _optional_string(item, key)


def _validate_table(data: dict[str, Any]) -> None:
    columns = data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not columns:
        raise WidgetError("table data must define a non-empty \"columns\" list")
    column_names = cast(list[object], columns)
    if len(column_names) > MAX_TABLE_COLUMNS:
        raise WidgetError(f"table data accepts at most {MAX_TABLE_COLUMNS} columns")
    if not all(isinstance(column, str) for column in column_names):
        raise WidgetError("table columns must be strings")
    if not isinstance(rows, list):
        raise WidgetError("table data must define a \"rows\" list")
    row_values = cast(list[object], rows)
    if len(row_values) > MAX_TABLE_ROWS:
        raise WidgetError(f"table data accepts at most {MAX_TABLE_ROWS} rows")
    for entry in row_values:
        if not isinstance(entry, list):
            raise WidgetError("each table row must match the number of columns")
        cells = cast(list[object], entry)
        if len(cells) != len(column_names):
            raise WidgetError("each table row must match the number of columns")
        if not all(
            isinstance(cell, (str, int, float)) and not isinstance(cell, bool)
            for cell in cells
        ):
            raise WidgetError("table cells must be strings or numbers")


def _validate_text(data: dict[str, Any]) -> None:
    text = data.get("text")
    if not isinstance(text, str):
        raise WidgetError("text data must be {\"text\": \"...\"}")
    if len(text) > MAX_TEXT_CHARS:
        raise WidgetError(f"text data accepts at most {MAX_TEXT_CHARS} characters")


def _validate_metrics(data: dict[str, Any]) -> None:
    columns = data.get("columns", 2)
    if isinstance(columns, bool) or not isinstance(columns, int) or columns not in (1, 2, 3):
        raise WidgetError("metrics data columns must be 1, 2, or 3")
    for entry in _tile_items(data, "metrics", MAX_TILE_ITEMS):
        _require_type(entry, "label", (str,))
        _require_type(entry, "value", (str, int, float))
        _optional_string(entry, "detail")
        _optional_tone(entry)


def _validate_progress(data: dict[str, Any]) -> None:
    for entry in _tile_items(data, "progress", MAX_TILE_ITEMS):
        _require_type(entry, "label", (str,))
        if _number(entry, "value") < 0:
            raise WidgetError("progress values must not be negative")
        if entry.get("max") is not None and _number(entry, "max") <= 0:
            raise WidgetError("progress max must be greater than 0")
        _optional_string(entry, "detail")
        _optional_tone(entry)


def _validate_facts(data: dict[str, Any]) -> None:
    for entry in _tile_items(data, "facts", MAX_FACT_ITEMS):
        _require_type(entry, "label", (str,))
        _require_type(entry, "value", (str, int, float))
        _optional_tone(entry)


def _validate_chart(data: dict[str, Any]) -> None:
    kind = data.get("type", "bar")
    if kind not in ("bar", "line"):
        raise WidgetError('chart data type must be "bar" or "line"')
    for entry in _tile_items(data, "chart", MAX_CHART_POINTS):
        _require_type(entry, "label", (str,))
        if _number(entry, "value") < 0:
            raise WidgetError("chart values must not be negative")
    if data.get("max") is not None and _number(data, "max") <= 0:
        raise WidgetError("chart max must be greater than 0")


def _validate_timeline(data: dict[str, Any]) -> None:
    for entry in _tile_items(data, "timeline", MAX_TIMELINE_ITEMS):
        _require_type(entry, "title", (str,))
        for key in ("detail", "meta"):
            _optional_string(entry, key)
        _optional_tone(entry)


def _validate_badges(data: dict[str, Any]) -> None:
    for entry in _tile_items(data, "badges", MAX_BADGE_ITEMS):
        _require_type(entry, "label", (str,))
        _optional_string(entry, "detail")
        _optional_tone(entry)


def _validate_links(data: dict[str, Any]) -> None:
    for entry in _tile_items(data, "links", MAX_LINK_ITEMS):
        _require_type(entry, "label", (str,))
        href = entry.get("href")
        if not isinstance(href, str) or not href.startswith(("https://", "http://")):
            raise WidgetError("each link needs an http(s) href")
        if len(href) > MAX_URL_CHARS:
            raise WidgetError(f"link href must be at most {MAX_URL_CHARS} characters")
        _optional_string(entry, "detail")
        _optional_string(entry, "meta")


def _validate_panel(data: dict[str, Any]) -> None:
    for key in ("lead", "note"):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise WidgetError(f'panel data "{key}" must be a non-empty string')
    blocks = data.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise WidgetError("panel data must define a non-empty \"blocks\" list")
    entries = cast(list[object], blocks)
    if len(entries) > MAX_PANEL_BLOCKS:
        raise WidgetError(f"panel data accepts at most {MAX_PANEL_BLOCKS} blocks")
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise WidgetError(f"panel block {index} must be an object")
        block = cast(dict[str, Any], entry)
        kind = block.get("kind")
        if kind not in _PANEL_BLOCK_KINDS:
            raise WidgetError(
                f"panel block {index} kind must be one of: "
                + ", ".join(sorted(_PANEL_BLOCK_KINDS))
            )
        title = block.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise WidgetError(f"panel block {index} title must be a non-empty string")
        if kind == "divider":
            continue
        try:
            _validate_block(cast(str, kind), block)
        except WidgetError as exc:
            raise WidgetError(f"panel block {index}: {exc}") from None


def _tile_items(data: dict[str, Any], kind: str, limit: int) -> list[dict[str, Any]]:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise WidgetError(f'{kind} data must define a non-empty "items" list')
    entries = cast(list[object], items)
    if len(entries) > limit:
        raise WidgetError(f"{kind} data accepts at most {limit} items")
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise WidgetError(f"each {kind} item must be an object")
        rows.append(cast(dict[str, Any], entry))
    return rows


def parse_stdout_json(text: str) -> object:
    if not text.strip():
        raise WidgetError("command produced no output; print one JSON value")
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WidgetError("command output is not valid JSON") from exc
    _validate_json_size(value)
    return value


def parse_definition(raw: object, *, expected_id: str | None = None) -> WidgetDefinition:
    """Normalize a stored manifest at the filesystem boundary."""
    if not isinstance(raw, dict):
        raise WidgetError("widget manifest must be an object")
    data = cast(dict[str, Any], raw)
    if data.get("schema_version") != 1:
        raise WidgetError("unsupported widget manifest version")
    widget_id = validate_widget_id(data.get("id"))
    if expected_id is not None and widget_id != expected_id:
        raise WidgetError("widget manifest id does not match its directory")
    view = validate_view(data.get("view"))
    command = validate_command(data.get("command"))
    timeout = validate_timeout(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    every_seconds = validate_refresh_interval(data.get("every_seconds"))
    workspace = data.get("workspace")
    if workspace is not None and (
        not isinstance(workspace, str) or not workspace or not Path(workspace).is_absolute()
    ):
        raise WidgetError("workspace must be an absolute path or null")
    created_at = data.get("created_at")
    updated_at = data.get("updated_at")
    stamp = now_iso()
    return WidgetDefinition(
        id=widget_id,
        title=validate_title(data.get("title")),
        description=validate_description(data.get("description")),
        view=view,
        command=command,
        timeout_seconds=timeout,
        every_seconds=every_seconds,
        workspace=workspace,
        created_at=created_at if isinstance(created_at, str) else stamp,
        updated_at=updated_at if isinstance(updated_at, str) else stamp,
    )


def parse_envelope(raw: object) -> tuple[object, int, str | None] | None:
    """Return ``(data, updated_at_ms, error)`` from a stored data envelope."""
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    if "data" not in data:
        return None
    updated_at = data.get("updated_at_ms")
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = 0
    error = data.get("error")
    return data["data"], updated_at, error if isinstance(error, str) else None



def dump_json(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WidgetError("data must be JSON-serializable") from exc
    _validate_json_size(value, encoded)
    return encoded


def _reject_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _validate_json_size(value: object, encoded: str | None = None) -> None:
    if encoded is None:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise WidgetError("data must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_DATA_BYTES:
        raise WidgetError(f"data must be at most {MAX_DATA_BYTES // 1024} KB")


def _require_type(data: dict[str, Any], key: str, types: tuple[type, ...]) -> None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, types):
        raise WidgetError(f"data.{key} must be a {types[0].__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise WidgetError(f"data.{key} must be a finite number")


def _optional_string(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise WidgetError(f"data.{key} must be a string")


def _optional_tone(data: dict[str, Any]) -> None:
    value = data.get("tone")
    if value is not None and value not in _TONES:
        raise WidgetError("data.tone must be one of: " + ", ".join(sorted(_TONES)))


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WidgetError(f"data.{key} must be a finite number")
    return float(value)
