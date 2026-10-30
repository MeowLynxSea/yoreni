"""WidgetService lifecycle, validation, and refresh scheduling."""

import asyncio
import sys

import pytest

from yoreni.runtime_admission import RuntimeAdmission
from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.service import WidgetService
from yoreni.widgets.store import WidgetStore
from yoreni.widgets.types import (
    WidgetError,
    WidgetNotFoundError,
    WidgetPatch,
    WidgetSpec,
)

_COUNTER = """\
import json
import pathlib

path = pathlib.Path("count.txt")
count = int(path.read_text()) + 1 if path.exists() else 1
path.write_text(str(count))
print(json.dumps({"text": str(count)}))
"""


def _spec(**overrides: object) -> WidgetSpec:
    values: dict[str, object] = {
        "id": "counter",
        "title": "Counter",
        "view": "text",
        "script": _COUNTER,
        "command": (sys.executable, "data.py"),
    }
    values.update(overrides)
    return WidgetSpec(**values)


@pytest.fixture
def store(tmp_path):
    return WidgetStore(tmp_path / "widgets")


@pytest.fixture
def changes():
    return []


@pytest.fixture
def service(tmp_path, store, changes):
    async def on_change(change):
        changes.append(change)

    return WidgetService(
        store,
        runner=WidgetRunner(),
        workspace=tmp_path,
        on_change=on_change,
    )


async def test_create_runs_the_command_and_caches_data(service, changes):
    definition, result = await service.create(_spec())

    assert definition.id == "counter"
    assert definition.command == (sys.executable, "data.py")
    assert result is not None
    assert result.error is None
    assert result.data == {"text": "1"}

    snapshot = service.snapshot("counter")
    assert snapshot.data == {"text": "1"}
    assert snapshot.summary.has_data
    assert snapshot.summary.error is None
    assert snapshot.summary.updated_at_ms
    assert [change.kind for change in changes] == ["created", "data"]


async def test_create_rejects_a_duplicate_id(service):
    await service.create(_spec())

    with pytest.raises(WidgetError, match="already exists"):
        await service.create(_spec())


async def test_create_rejects_reserved_and_invalid_ids(service):
    with pytest.raises(WidgetError, match="built-in"):
        await service.create(_spec(id="goal"))
    with pytest.raises(WidgetError, match="widget ids"):
        await service.create(_spec(id="../counter"))


async def test_create_rejects_missing_or_misplaced_html(service):
    with pytest.raises(WidgetError, match="html is required"):
        await service.create(_spec(view="html", script=None, command=None))
    with pytest.raises(WidgetError, match="only allowed"):
        await service.create(_spec(html="<p>x</p>"))


async def test_create_rejects_an_unknown_workspace(service, tmp_path):
    with pytest.raises(WidgetError, match="does not exist"):
        await service.create(_spec(workspace=str(tmp_path / "missing")))


async def test_create_rejects_more_than_the_widget_limit(service, monkeypatch):
    monkeypatch.setattr("yoreni.widgets.service.MAX_WIDGETS", 1)
    await service.create(_spec(id="notes", script=None, command=None, title="Notes"))

    with pytest.raises(WidgetError, match="at most 1 widgets"):
        await service.create(_spec(id="second", script=None, command=None, title="Second"))


async def test_title_update_does_not_run_the_command(service, changes):
    await service.create(_spec())

    definition, result = await service.update("counter", WidgetPatch(title="Renamed"))

    assert definition.title == "Renamed"
    assert result is None
    assert service.snapshot("counter").data == {"text": "1"}
    assert [change.kind for change in changes] == ["created", "data", "updated"]


async def test_script_update_rewrites_the_running_script(store, service):
    await service.create(_spec())

    _, result = await service.update("counter", WidgetPatch(script=_COUNTER))
    assert result is not None
    assert result.error is None
    assert result.data == {"text": "2"}
    assert store.read_script("counter", "data.py") == _COUNTER


async def test_update_can_move_the_script_to_another_file(store, service):
    await service.create(_spec())

    _, result = await service.update(
        "counter",
        WidgetPatch(
            script=_COUNTER,
            script_filename="collect.py",
            command=(sys.executable, "collect.py"),
        ),
    )

    assert result is not None
    assert result.data == {"text": "2"}
    assert store.read_script("counter", "collect.py") == _COUNTER


async def test_failed_refresh_keeps_the_previous_data(service):
    await service.create(_spec())

    _, result = await service.update(
        "counter",
        WidgetPatch(command=(sys.executable, "-c", "import sys; sys.exit(2)")),
    )

    assert result is not None
    assert result.error is not None
    assert "status 2" in result.error
    snapshot = service.snapshot("counter")
    assert snapshot.data == {"text": "1"}
    assert snapshot.summary.error is not None
    assert "status 2" in snapshot.summary.error


async def test_refresh_requires_a_command(service):
    await service.create(_spec(id="notes", script=None, command=None, title="Notes"))

    result = await service.refresh("notes")

    assert result.data is None
    assert result.error == "widget has no command"


async def test_set_data_validates_the_template_contract(service):
    await service.create(_spec())

    with pytest.raises(WidgetError, match="text data"):
        await service.set_data("counter", {"items": []})

    await service.set_data("counter", {"text": "manual"})

    snapshot = service.snapshot("counter")
    assert snapshot.data == {"text": "manual"}
    assert snapshot.summary.error is None


async def test_html_widget_stores_markup_and_accepts_any_object(service):
    await service.create(
        _spec(view="html", html="<p>hello</p>", script=None, command=None)
    )

    await service.set_data("counter", {"anything": [1, 2, 3]})

    snapshot = service.snapshot("counter")
    assert snapshot.html == "<p>hello</p>"
    assert snapshot.data == {"anything": [1, 2, 3]}


async def test_delete_removes_the_widget(service, changes):
    await service.create(_spec())

    await service.delete("counter")

    with pytest.raises(WidgetNotFoundError):
        service.snapshot("counter")
    assert [change.kind for change in changes][-1] == "deleted"


async def test_scheduler_refreshes_due_widgets(service):
    await service.create(_spec(every_seconds=30))
    assert service.snapshot("counter").summary.next_run_at_ms is not None

    service._next_runs["counter"] = 0
    await service._tick()

    assert service.snapshot("counter").data == {"text": "2"}
    assert service.snapshot("counter").summary.next_run_at_ms is not None


async def test_scheduler_waits_for_runtime_admission(tmp_path, store):
    admission = RuntimeAdmission()
    service = WidgetService(
        store,
        runner=WidgetRunner(),
        workspace=tmp_path,
        admission=admission,
    )
    await service.create(_spec(every_seconds=30))
    service._next_runs["counter"] = 0

    with admission.pause_before_start():
        tick = asyncio.create_task(service._tick())
        await asyncio.sleep(0.05)
        assert not tick.done()
        assert service.snapshot("counter").data == {"text": "1"}

    await asyncio.wait_for(tick, timeout=5)
    assert service.snapshot("counter").data == {"text": "2"}
