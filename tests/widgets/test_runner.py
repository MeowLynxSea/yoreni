"""WidgetRunner command execution and failure reporting."""

import sys
import time
from pathlib import Path

from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.types import WidgetDefinition


def _definition(
    command: list[str], *, timeout_seconds: int = 30
) -> WidgetDefinition:
    return WidgetDefinition(
        id="probe",
        title="Probe",
        description="",
        view="text",
        command=tuple(command),
        timeout_seconds=timeout_seconds,
        every_seconds=None,
        workspace=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


async def test_json_stdout_becomes_data(tmp_path):
    command = [sys.executable, "-c", "import json; print(json.dumps({'text': 'ok'}))"]

    result = await WidgetRunner().run(
        _definition(command), widget_dir=tmp_path, workspace=None
    )

    assert result.error is None
    assert result.data == {"text": "ok"}
    assert result.duration_ms >= 0


async def test_command_runs_inside_the_widget_directory(tmp_path):
    command = [
        sys.executable,
        "-c",
        "import json, os; print(json.dumps({'cwd': os.getcwd(), 'home': os.environ['HOME']}))",
    ]

    result = await WidgetRunner().run(
        _definition(command), widget_dir=tmp_path, workspace=None
    )

    assert result.error is None
    assert Path(result.data["cwd"]).resolve() == tmp_path.resolve()
    assert Path(result.data["home"]).resolve() == tmp_path.resolve()


async def test_non_json_stdout_is_an_error(tmp_path):
    command = [sys.executable, "-c", "print('not json')"]

    result = await WidgetRunner().run(
        _definition(command), widget_dir=tmp_path, workspace=None
    )

    assert result.data is None
    assert result.error is not None
    assert "not valid JSON" in result.error


async def test_empty_stdout_is_an_error(tmp_path):
    command = [sys.executable, "-c", "pass"]

    result = await WidgetRunner().run(
        _definition(command), widget_dir=tmp_path, workspace=None
    )

    assert result.data is None
    assert result.error is not None
    assert "produced no output" in result.error


async def test_missing_executable_is_an_error(tmp_path):
    result = await WidgetRunner().run(
        _definition(["yoreni-missing-widget-binary"]), widget_dir=tmp_path, workspace=None
    )

    assert result.data is None
    assert result.error is not None
    assert "executable not found" in result.error


async def test_failed_command_reports_status_and_stderr(tmp_path):
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('boom'); sys.exit(3)",
    ]

    result = await WidgetRunner().run(
        _definition(command), widget_dir=tmp_path, workspace=None
    )

    assert result.data is None
    assert result.error is not None
    assert "status 3" in result.error
    assert "boom" in result.error


async def test_timeout_kills_the_command(tmp_path):
    started = time.monotonic()

    result = await WidgetRunner().run(
        _definition([sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=1),
        widget_dir=tmp_path,
        workspace=None,
    )

    assert result.data is None
    assert result.error is not None
    assert "timed out after 1s" in result.error
    assert time.monotonic() - started < 10
