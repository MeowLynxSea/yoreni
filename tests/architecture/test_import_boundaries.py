"""Regression coverage for the executable Python import boundaries."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_import_boundaries import Violation, find_violations


@pytest.mark.parametrize(
    ("source", "target", "line"),
    [
        ("import yoreni.webui.metadata", "yoreni.webui.metadata", 1),
        ("from yoreni import webui", "yoreni.webui", 1),
        ("from yoreni.webui import metadata", "yoreni.webui.metadata", 1),
        ("from .. import webui", "yoreni.webui", 1),
        ("from ..webui import metadata", "yoreni.webui.metadata", 1),
        (
            "def load():\n    from yoreni.cli import gateway",
            "yoreni.cli.gateway", 2,
        ),
        (
            "import importlib\nimportlib.import_module('yoreni.webui.metadata')",
            "yoreni.webui.metadata", 2,
        ),
        (
            "import importlib as modules\n"
            "modules.import_module(name='yoreni.webui.metadata')",
            "yoreni.webui.metadata", 2,
        ),
        (
            "from importlib import import_module as load\n"
            "load('..webui.metadata', __package__)",
            "yoreni.webui.metadata", 2,
        ),
        (
            "from importlib import import_module\n"
            "import_module('..webui.metadata', package='yoreni.agent')",
            "yoreni.webui.metadata", 2,
        ),
        ("__import__('yoreni.webui.metadata')", "yoreni.webui.metadata", 1),
        (
            "import builtins as imports\nimports.__import__('yoreni.webui.metadata')",
            "yoreni.webui.metadata", 2,
        ),
    ],
)
def test_core_rejects_adapter_imports_in_supported_spellings(
    source: str, target: str, line: int,
) -> None:
    assert find_violations(source, "yoreni.agent.loop") == [
        Violation("core-execution", target, line),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "from typing import TYPE_CHECKING as TC\n"
        "if TC:\n    from yoreni.webui import metadata",
        "import typing as t\n"
        "if t.TYPE_CHECKING:\n    from yoreni.webui import metadata",
    ],
)
def test_core_boundary_includes_type_dependencies(source: str) -> None:
    assert find_violations(source, "yoreni.agent.runner") == [
        Violation("core-execution", "yoreni.webui.metadata", 3),
    ]


@pytest.mark.parametrize(
    ("module", "rule"),
    [
        ("yoreni.session.manager", "session-storage"),
        ("yoreni.session.recovery", "session-storage"),
        ("yoreni.security.workspace_policy", "security-primitives"),
        ("yoreni.agent.turn_delivery", "core-execution"),
        ("yoreni.channels.notification_routes", "channel-routing-contracts"),
        ("yoreni.config.schema", "configuration-contracts"),
        ("yoreni.agent.tools.cli_apps_config", "tool-configuration-contracts"),
        ("yoreni.utils.file_io", "shared-primitives"),
    ],
)
def test_storage_and_security_do_not_depend_on_adapters(module: str, rule: str) -> None:
    assert find_violations("from yoreni.cli import gateway", module) == [
        Violation(rule, "yoreni.cli.gateway", 1),
    ]


@pytest.mark.parametrize(
    "dependency",
    [
        "yoreni.bus.outbound_events.ProgressEvent",
        "yoreni.command.router",
        "yoreni.pairing.events.PairingChanged",
        "yoreni.session.webui_turns.clear_websocket_turn_if_current",
        "yoreni.webui.transcript.WebUITranscriptRecorder",
        "yoreni.webui.outbound_wire.project_notification",
    ],
)
def test_websocket_transport_leaves_event_and_history_policy_to_delivery(dependency: str) -> None:
    module, name = dependency.rsplit(".", 1)
    source = f"from {module} import {name}\n"
    assert find_violations(source, "yoreni.channels.websocket.runtime") == [
        Violation("websocket-transport", dependency, 1),
    ]
    assert find_violations(source, "yoreni.webui.outbound_delivery") == []


@pytest.mark.parametrize(
    "source",
    [
        "from yoreni.config.schema import Base",
        "from ...config.schema import Base as ConfigBase",
    ],
)
def test_tool_configs_import_the_shared_base_directly(source: str) -> None:
    assert find_violations(source, "yoreni.agent.tools.shell") == [
        Violation("tool-config-base", "yoreni.config.schema.Base", 1),
    ]
    assert find_violations(
        "from yoreni.config_base import Base", "yoreni.agent.tools.shell",
    ) == []


def test_configuration_imports_the_six_tool_owned_config_contracts() -> None:
    source = (
        "from yoreni.agent.tools.cli_apps_config import CliAppsToolConfig\n"
        "from yoreni.agent.tools.filesystem_config import FileToolsConfig\n"
        "from yoreni.agent.tools.image_generation_config import ImageGenerationToolConfig\n"
        "from yoreni.agent.tools.self_config import MyToolConfig\n"
        "from yoreni.agent.tools.shell_config import ExecToolConfig\n"
        "from yoreni.agent.tools.web_config import WebToolsConfig\n"
    )
    assert find_violations(source, "yoreni.config.schema") == []


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("yoreni.agent.loop", "from yoreni.session.recovery import RecoveryAdmission"),
        ("yoreni.session.manager", "from yoreni.providers.base import ProviderConversationState"),
        ("yoreni.session.webui_turns", "from yoreni.webui import transcript"),
        ("yoreni.cli.gateway_runtime", "from yoreni.agent.loop import AgentLoop"),
        ("yoreni.channels.websocket.runtime", "from yoreni.webui import inbound_commands"),
        ("yoreni.agent.tools.cli_apps_config", "from yoreni.config_base import Base"),
        (
            "yoreni.agent.turn_delivery",
            "from yoreni.channels.notification_routes import notification_metadata",
        ),
    ],
)
def test_existing_runtime_and_composition_dependencies_remain_allowed(
    module: str, source: str,
) -> None:
    assert find_violations(source, module) == []


@pytest.mark.parametrize(
    ("relative", "source", "rule", "target", "line"),
    [
        (
            "yoreni/agent/runner.py", "import yoreni.webui.metadata",
            "core-execution", "yoreni.webui.metadata", 1,
        ),
        (
            "yoreni/agent/loop.py",
            "import importlib\nif importlib.import_module('yoreni.webui.metadata'):\n    pass",
            "core-execution", "yoreni.webui.metadata", 2,
        ),
        (
            "yoreni/agent/loop.py", "if __import__('yoreni.webui.metadata'):\n    pass",
            "core-execution", "yoreni.webui.metadata", 1,
        ),
        (
            "yoreni/session/manager.py", "from yoreni.cli import gateway",
            "session-storage", "yoreni.cli.gateway", 1,
        ),
        (
            "yoreni/channels/notification_routes.py",
            "from yoreni.channels.slack.runtime import SlackChannel",
            "channel-routing-contracts", "yoreni.channels.slack.runtime.SlackChannel", 1,
        ),
        (
            "yoreni/channels/notification_routes.py",
            "from yoreni.agent.turn_delivery import TurnRoute",
            "channel-routing-contracts", "yoreni.agent.turn_delivery.TurnRoute", 1,
        ),
        (
            "yoreni/security/__init__.py", "from ..cli import gateway",
            "security-primitives", "yoreni.cli.gateway", 1,
        ),
        (
            "yoreni/channels/websocket/runtime.py",
            "import importlib as modules\nmodules.import_module('yoreni.command.router')",
            "websocket-transport", "yoreni.command.router", 2,
        ),
        (
            "yoreni/agent/tools/shell.py", "from ...config.schema import Base as ConfigBase",
            "tool-config-base", "yoreni.config.schema.Base", 1,
        ),
        (
            "yoreni/channels/telegram/runtime.py", "from yoreni.config.schema import Base",
            "tool-config-base", "yoreni.config.schema.Base", 1,
        ),
        (
            "yoreni/config/schema.py", "from yoreni.agent.tools.shell import ExecTool",
            "configuration-contracts", "yoreni.agent.tools.shell.ExecTool", 1,
        ),
        (
            "yoreni/agent/tools/cli_apps_config.py", "from .cli_apps import CliAppsTool",
            "tool-configuration-contracts", "yoreni.agent.tools.cli_apps.CliAppsTool", 1,
        ),
        (
            "yoreni/agent/tools/web_config.py", "from yoreni.config.schema import Config",
            "tool-configuration-contracts", "yoreni.config.schema.Config", 1,
        ),
        (
            "yoreni/providers/oauth_catalog_cache.py",
            "from yoreni.providers import openai_codex_provider",
            "oauth-catalog-cache", "yoreni.providers.openai_codex_provider", 1,
        ),
    ],
)
def test_cli_rejects_a_violation_and_reports_its_source_location(
    tmp_path: Path, relative: str, source: str, rule: str, target: str, line: int,
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_import_boundaries", "--root", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr.strip() == f"{relative}:{line}: [{rule}] must not import {target}"


def test_cli_accepts_a_tool_owned_config_import(tmp_path: Path) -> None:
    path = tmp_path / "yoreni/config/schema.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from yoreni.agent.tools.cli_apps_config import CliAppsToolConfig\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_import_boundaries", "--root", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
