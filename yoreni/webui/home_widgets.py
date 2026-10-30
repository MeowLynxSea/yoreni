"""Persisted Home widget layout shared by every WebUI client."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, cast

from loguru import logger

from yoreni.config.paths import get_webui_dir

HOME_WIDGET_LAYOUT_SCHEMA_VERSION = 1
_MAX_LAYOUT_FILE_BYTES = 32 * 1024
_MAX_WIDGETS = 32
_WIDGET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_WIDGET_STATE_KEYS = ("visible", "collapsed")


def home_widget_layout_path() -> Path:
    return get_webui_dir() / "home-widgets.json"


def default_home_widget_layout() -> dict[str, Any]:
    return {
        "schema_version": HOME_WIDGET_LAYOUT_SCHEMA_VERSION,
        "updated_at": None,
        "order": [],
        "widgets": {},
    }


def _valid_widget_id(value: object) -> str | None:
    if not isinstance(value, str) or _WIDGET_ID_RE.fullmatch(value) is None:
        return None
    return value


def _valid_widget_state(value: object) -> dict[str, bool] | None:
    if not isinstance(value, dict):
        return None
    raw = cast(dict[str, Any], value)
    state: dict[str, bool] = {}
    for key in _WIDGET_STATE_KEYS:
        flag = raw.get(key)
        if not isinstance(flag, bool):
            return None
        state[key] = flag
    return state


def normalize_home_widget_layout(raw: Any) -> dict[str, Any]:
    """Keep only well-formed widget entries and a matching, complete order."""
    if not isinstance(raw, dict):
        raw = {}
    raw = cast(dict[str, Any], raw)
    layout = default_home_widget_layout()
    updated_at = raw.get("updated_at")
    layout["updated_at"] = updated_at if isinstance(updated_at, str) else None

    widgets: dict[str, dict[str, bool]] = {}
    raw_widgets = raw.get("widgets")
    if isinstance(raw_widgets, dict):
        for key, value in cast(dict[object, object], raw_widgets).items():
            widget_id = _valid_widget_id(key)
            state = _valid_widget_state(value)
            if widget_id is None or state is None or widget_id in widgets:
                continue
            widgets[widget_id] = state
            if len(widgets) >= _MAX_WIDGETS:
                break
    layout["widgets"] = widgets

    order: list[str] = []
    raw_order = raw.get("order")
    if isinstance(raw_order, list):
        for value in cast(list[object], raw_order):
            widget_id = _valid_widget_id(value)
            if widget_id is not None and widget_id in widgets and widget_id not in order:
                order.append(widget_id)
    order.extend(widget_id for widget_id in widgets if widget_id not in order)
    layout["order"] = order
    return layout


def read_home_widget_layout() -> dict[str, Any]:
    path = home_widget_layout_path()
    if not path.is_file():
        return default_home_widget_layout()
    try:
        if path.stat().st_size > _MAX_LAYOUT_FILE_BYTES:
            logger.warning("webui home widget layout too large, ignoring: {}", path)
            return default_home_widget_layout()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read webui home widget layout failed {}: {}", path, e)
        return default_home_widget_layout()
    return normalize_home_widget_layout(raw)


def write_home_widget_layout(raw: dict[str, Any]) -> dict[str, Any]:
    layout = normalize_home_widget_layout(raw)
    layout["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(layout, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_LAYOUT_FILE_BYTES:
        raise ValueError("home widget layout is too large")

    path = home_widget_layout_path()
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
        return layout
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return layout


def update_home_widget_layout(order: object, widgets: object) -> dict[str, Any]:
    """Replace the stored layout with a client snapshot of every known widget."""
    if not isinstance(widgets, dict):
        raise ValueError("widgets must be an object")
    raw_widgets = cast(dict[object, object], widgets)
    if len(raw_widgets) > _MAX_WIDGETS:
        raise ValueError(f"at most {_MAX_WIDGETS} widgets can be stored")

    states: dict[str, dict[str, bool]] = {}
    for key, value in raw_widgets.items():
        widget_id = _valid_widget_id(key)
        if widget_id is None:
            raise ValueError("widget ids must be 1-64 letters, digits, '_' or '-'")
        state = _valid_widget_state(value)
        if state is None:
            raise ValueError(f"widget '{widget_id}' must define boolean visible and collapsed")
        states[widget_id] = state

    if not isinstance(order, list):
        raise ValueError("order must be a list of widget ids")
    ordered: list[str] = []
    for value in cast(list[object], order):
        widget_id = _valid_widget_id(value)
        if widget_id is None:
            raise ValueError("order must contain widget ids")
        if widget_id in ordered:
            raise ValueError(f"widget '{widget_id}' appears twice in order")
        ordered.append(widget_id)
    if set(ordered) != set(states):
        raise ValueError("order must list every widget exactly once")

    return write_home_widget_layout({
        "schema_version": HOME_WIDGET_LAYOUT_SCHEMA_VERSION,
        "widgets": states,
        "order": ordered,
    })


def home_widget_layout_payload() -> dict[str, Any]:
    return read_home_widget_layout()
