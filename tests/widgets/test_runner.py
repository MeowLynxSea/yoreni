"""WidgetRunner command execution and failure reporting inside the VM."""

import sys
import time

import pytest

from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.gateway import SandboxGateway, set_sandbox_gateway
from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.types import WidgetDefinition

pytestmark = pytest.mark.usefixtures("vm_workspace")


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

    result = await WidgetRunner().run(_definition(command), widget_dir=tmp_path)

    assert result.error is None
    assert result.data == {"text": "ok"}
    assert result.duration_ms >= 0


async def test_the_command_runs_in_its_own_copy_inside_the_workspace(tmp_path):
    (tmp_path / "data.py").write_text(
        "import json, os\n"
        "print(json.dumps({'cwd': os.getcwd(), 'home': os.environ['HOME']}))\n",
        encoding="utf-8",
    )

    result = await WidgetRunner().run(
        _definition([sys.executable, "data.py"]), widget_dir=tmp_path
    )

    expected = tmp_path / ".yoreni" / "widgets" / "probe"
    assert result.error is None
    assert result.data == {"cwd": str(expected), "home": str(expected)}


async def test_non_json_stdout_is_an_error(tmp_path):
    command = [sys.executable, "-c", "print('not json')"]

    result = await WidgetRunner().run(_definition(command), widget_dir=tmp_path)

    assert result.data is None
    assert result.error is not None
    assert "not valid JSON" in result.error


async def test_empty_stdout_is_an_error(tmp_path):
    command = [sys.executable, "-c", "pass"]

    result = await WidgetRunner().run(_definition(command), widget_dir=tmp_path)

    assert result.data is None
    assert result.error is not None
    assert "produced no output" in result.error


async def test_failed_command_reports_status_and_stderr(tmp_path):
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('boom'); sys.exit(3)",
    ]

    result = await WidgetRunner().run(_definition(command), widget_dir=tmp_path)

    assert result.data is None
    assert result.error is not None
    assert "status 3" in result.error
    assert "boom" in result.error


async def test_timeout_stops_the_command(tmp_path):
    started = time.monotonic()

    result = await WidgetRunner().run(
        _definition([sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=1),
        widget_dir=tmp_path,
    )

    assert result.data is None
    assert result.error is not None
    assert "timed out after 1s" in result.error
    assert time.monotonic() - started < 10


async def test_a_missing_vm_never_runs_the_command_on_the_host(tmp_path):
    class _DownBackend:
        name = "down"

        async def start(self) -> None:
            raise SandboxUnavailableError("no virtualization")

        async def stop(self) -> None: ...
        async def restart(self) -> None: ...
        async def status(self) -> object:
            raise SandboxUnavailableError("no virtualization")

        async def exec(self, *_args: object, **_kwargs: object) -> object:
            raise SandboxUnavailableError("no virtualization")

        async def start_exec(self, *_args: object, **_kwargs: object) -> object:
            raise SandboxUnavailableError("no virtualization")

        async def read_bytes(self, *_args: object, **_kwargs: object) -> bytes:
            raise SandboxUnavailableError("no virtualization")

        async def write_bytes(self, *_args: object, **_kwargs: object) -> None:
            raise SandboxUnavailableError("no virtualization")

        async def list_dir(self, *_args: object, **_kwargs: object) -> object:
            raise SandboxUnavailableError("no virtualization")

        async def stat(self, *_args: object, **_kwargs: object) -> object:
            raise SandboxUnavailableError("no virtualization")

        async def realpath(self, *_args: object, **_kwargs: object) -> str:
            raise SandboxUnavailableError("no virtualization")

        async def mkdir(self, *_args: object, **_kwargs: object) -> None:
            raise SandboxUnavailableError("no virtualization")

        async def remove(self, *_args: object, **_kwargs: object) -> None: ...
        async def rename(self, *_args: object, **_kwargs: object) -> None: ...
        async def forwarded_port(self, *_args: object, **_kwargs: object) -> int | None:
            return None

        def host_staging_dir(self) -> str:
            return "/tmp"

        async def copy_in(self, *_args: object, **_kwargs: object) -> None:
            raise SandboxUnavailableError("no virtualization")

        async def copy_out(self, *_args: object, **_kwargs: object) -> None:
            raise SandboxUnavailableError("no virtualization")

        async def close(self) -> None: ...

    gateway = SandboxGateway(
        SandboxConfig(
            enabled=True,
            backend="down",
            workspace=str(tmp_path),
            desktop_enabled=False,
            browser_enabled=False,
        ),
        backend=_DownBackend(),
    )
    set_sandbox_gateway(gateway)

    command = [sys.executable, "-c", "open('ran.txt', 'w').write('host')"]
    result = await WidgetRunner().run(_definition(command), widget_dir=tmp_path)

    assert result.data is None
    assert result.error is not None
    assert "no virtualization" in result.error
    assert not (tmp_path / "ran.txt").exists()
