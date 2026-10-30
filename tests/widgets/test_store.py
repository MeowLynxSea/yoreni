"""WidgetStore filesystem contracts."""

import json
import sys

import pytest

from yoreni.widgets.store import WidgetStore
from yoreni.widgets.types import MAX_HTML_BYTES, WidgetDefinition, WidgetError, WidgetNotFoundError


def _definition(widget_id: str = "weather", **overrides: object) -> WidgetDefinition:
    values: dict[str, object] = {
        "id": widget_id,
        "title": "Weather",
        "description": "",
        "view": "text",
        "command": None,
        "timeout_seconds": 30,
        "every_seconds": None,
        "workspace": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    values.update(overrides)
    return WidgetDefinition(**values)


def test_round_trip_persists_each_file(tmp_path):
    store = WidgetStore(tmp_path / "widgets")
    store.prepare("weather")
    store.write_html("weather", "<p>sunny</p>")
    store.write_script("weather", "data.py", "print('{}')")
    store.write_definition(_definition(view="html"))
    store.write_data("weather", {"text": "sunny"})

    assert store.ids() == ["weather"]
    assert store.read("weather").title == "Weather"
    assert store.read_html("weather") == "<p>sunny</p>"
    assert store.read_script("weather", "data.py") == "print('{}')"
    envelope = store.read_envelope("weather")
    assert envelope is not None
    data, updated_at_ms, error = envelope
    assert data == {"text": "sunny"}
    assert updated_at_ms > 0
    assert error is None


def test_missing_widget_reads_as_not_found(tmp_path):
    store = WidgetStore(tmp_path / "widgets")

    with pytest.raises(WidgetNotFoundError):
        store.read("ghost")
    assert store.read_html("ghost") is None
    assert store.read_envelope("ghost") is None
    with pytest.raises(WidgetNotFoundError):
        store.remove("ghost")


def test_remove_deletes_the_widget_directory(tmp_path):
    store = WidgetStore(tmp_path / "widgets")
    store.prepare("weather")
    store.write_definition(_definition())

    store.remove("weather")

    assert store.ids() == []
    with pytest.raises(WidgetNotFoundError):
        store.read("weather")


def test_remove_keeps_sibling_widgets(tmp_path):
    store = WidgetStore(tmp_path / "widgets")
    for widget_id in ("one", "two"):
        store.prepare(widget_id)
        store.write_definition(_definition(widget_id))

    store.remove("one")

    assert store.ids() == ["two"]


@pytest.mark.parametrize("widget_id", ["../escape", "a/b", "", "goal", ".trash-x", "x" * 65])
def test_invalid_widget_ids_never_build_paths(tmp_path, widget_id):
    store = WidgetStore(tmp_path / "widgets")

    with pytest.raises(WidgetError):
        store.prepare(widget_id)


@pytest.mark.parametrize("filename", ["../data.py", "sub/data.py", "", ".", "..", "x" * 65])
def test_invalid_script_filenames_never_build_paths(tmp_path, filename):
    store = WidgetStore(tmp_path / "widgets")
    store.prepare("weather")

    with pytest.raises(WidgetError):
        store.write_script("weather", filename, "print('hi')")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_symlinked_widget_directory_cannot_escape_the_root(tmp_path):
    root = tmp_path / "widgets"
    store = WidgetStore(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "widget.json").write_text(json.dumps(_definition("linked").to_manifest()))
    (root / "linked").symlink_to(outside, target_is_directory=True)

    assert store.ids() == []
    with pytest.raises(OSError):
        store.read("linked")
    with pytest.raises(OSError):
        store.write_data("linked", {"text": "overwritten"})
    assert not (outside / "data.json").exists()


def test_oversized_view_is_ignored_instead_of_loaded(tmp_path):
    store = WidgetStore(tmp_path / "widgets")
    store.prepare("weather")
    store.write_definition(_definition(view="html"))
    (tmp_path / "widgets" / "weather" / "view.html").write_text(
        "x" * (MAX_HTML_BYTES + 1), encoding="utf-8"
    )

    assert store.read_html("weather") is None
