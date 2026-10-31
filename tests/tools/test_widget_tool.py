"""WidgetTool actions, argument rules, and turn-workspace binding."""

import sys

import pytest

from yoreni.agent.tools.context import RequestContext, request_context
from yoreni.agent.tools.registry import ToolRegistry
from yoreni.agent.tools.widget import WidgetTool
from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.service import WidgetService
from yoreni.widgets.store import WidgetStore
from yoreni.widgets.types import WidgetNotFoundError

pytestmark = pytest.mark.usefixtures("widget_vm")

_STAT_COMMAND = [
    sys.executable,
    "-c",
    "import json; print(json.dumps({'value': 7, 'label': 'Runs'}))",
]

_COUNTER_SCRIPT = """\
import json
import pathlib

path = pathlib.Path("count.txt")
count = int(path.read_text()) + 1 if path.exists() else 1
path.write_text(str(count))
print(json.dumps({"text": str(count)}))
"""


def _make_tool(tmp_path):
    service = WidgetService(
        WidgetStore(tmp_path / "widgets"),
        runner=WidgetRunner(),
        workspace=tmp_path,
    )
    return WidgetTool(service, str(tmp_path)), service


def _create_counter(tool):
    return tool.execute(
        action="create",
        widget_id="counter",
        title="Counter",
        view="text",
        script=_COUNTER_SCRIPT,
        argv=[sys.executable, "data.py"],
    )


def _create_stat(tool, **overrides):
    params = {
        "action": "create",
        "widget_id": "runs",
        "title": "Runs",
        "view": "stat",
        "argv": _STAT_COMMAND,
    }
    params.update(overrides)
    return tool.execute(**params)


async def test_create_stores_and_runs_the_command(tmp_path):
    tool, service = _make_tool(tmp_path)

    result = await _create_stat(tool)

    assert isinstance(result, str)
    assert "Created widget 'Runs' (id: runs)." in result
    assert '"value": 7' in result
    assert service.snapshot("runs").data == {"value": 7, "label": "Runs"}


async def test_create_defaults_argv_for_a_script(tmp_path):
    tool, service = _make_tool(tmp_path)

    await tool.execute(
        action="create",
        widget_id="counter",
        title="Counter",
        view="text",
        script=_COUNTER_SCRIPT,
    )

    definition = service.snapshot("counter").summary.definition
    assert definition.command == ("python3", "data.py")


async def test_create_binds_the_turn_workspace(tmp_path):
    tool, service = _make_tool(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    with request_context(
        RequestContext(channel="websocket", chat_id="chat-1", workspace=project)
    ):
        await _create_stat(tool)

    definition = service.snapshot("runs").summary.definition
    assert definition.workspace == str(project.resolve())


async def test_get_returns_manifest_data_and_script(tmp_path):
    tool, _ = _make_tool(tmp_path)
    await _create_counter(tool)

    result = await tool.execute(action="get", widget_id="counter")

    assert "Widget 'Counter' (id: counter)" in result
    assert "View: text" in result
    assert '"text": "1"' in result
    assert "Script (data.py):" in result
    assert "count = int(path.read_text())" in result


async def test_update_changes_only_the_title(tmp_path):
    tool, service = _make_tool(tmp_path)
    await _create_counter(tool)

    result = await tool.execute(
        action="update", widget_id="counter", title="Daily counter"
    )

    assert "Updated widget 'Daily counter' (id: counter)." in result
    assert service.snapshot("counter").data == {"text": "1"}


async def test_update_clears_the_command_with_empty_argv(tmp_path):
    tool, service = _make_tool(tmp_path)
    await _create_stat(tool)

    await tool.execute(action="update", widget_id="runs", argv=[])

    definition = service.snapshot("runs").summary.definition
    assert definition.command is None


async def test_set_data_validates_and_stores_the_payload(tmp_path):
    tool, service = _make_tool(tmp_path)
    await _create_stat(tool)

    failure = await tool.execute(
        action="set_data", widget_id="runs", data={"items": []}
    )
    await tool.execute(
        action="set_data", widget_id="runs", data={"value": 9, "label": "Today"}
    )

    assert "Error:" in failure
    assert service.snapshot("runs").data == {"value": 9, "label": "Today"}


async def test_refresh_reports_a_failing_command(tmp_path):
    tool, _ = _make_tool(tmp_path)
    await _create_stat(tool, argv=[sys.executable, "-c", "import sys; sys.exit(4)"])

    result = await tool.execute(action="refresh", widget_id="runs")

    assert "Error: refresh failed for widget 'runs'" in result
    assert "status 4" in result


async def test_delete_removes_the_widget(tmp_path):
    tool, service = _make_tool(tmp_path)
    await _create_stat(tool)

    result = await tool.execute(action="delete", widget_id="runs")

    assert result == "Deleted widget 'runs'."
    with pytest.raises(WidgetNotFoundError):
        service.snapshot("runs")


async def test_unknown_widget_reports_a_list_hint(tmp_path):
    tool, _ = _make_tool(tmp_path)

    result = await tool.execute(action="get", widget_id="ghost")

    assert "not found" in result
    assert "action='list'" in result


async def test_list_marks_widgets_without_data(tmp_path):
    tool, _ = _make_tool(tmp_path)
    await tool.execute(
        action="create", widget_id="notes", title="Notes", view="text"
    )

    result = await tool.execute(action="list")

    assert "- notes — Notes (text)" in result
    assert "no data yet" in result


@pytest.fixture
def registry(tmp_path):
    tool, _ = _make_tool(tmp_path)
    reg = ToolRegistry()
    reg.register(tool)
    with request_context(
        RequestContext(channel="websocket", chat_id="chat-1", turn_id="turn-1")
    ):
        yield reg


def test_schema_accepts_list_with_only_action(registry):
    _, _, err = registry.prepare_call("widget", {"action": "list"})
    assert err is None


def test_schema_rejects_create_without_title(registry):
    _, _, err = registry.prepare_call(
        "widget", {"action": "create", "widget_id": "x", "view": "text"}
    )
    assert err is not None
    assert "title" in err


def test_schema_rejects_set_data_without_data(registry):
    _, _, err = registry.prepare_call("widget", {"action": "set_data", "widget_id": "x"})
    assert err is not None
    assert "data" in err


def test_schema_rejects_script_filename_without_script(registry):
    _, _, err = registry.prepare_call(
        "widget",
        {
            "action": "create",
            "widget_id": "x",
            "title": "X",
            "view": "text",
            "script_filename": "data.js",
        },
    )
    assert err is not None
    assert "script_filename" in err
