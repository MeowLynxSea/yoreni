import subprocess
import sys
from importlib import import_module

import pytest


def test_config_base_import_does_not_load_config_schema():
    code = """
import sys
from yoreni.config_base import Base
print("yoreni.config.schema" in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_config_validation_and_schema_generation_do_not_import_runtime_implementations():
    code = """
import importlib.abc
import sys

class RuntimeImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        config_modules = {
            'yoreni.agent', 'yoreni.agent.tools',
            'yoreni.agent.tools.cli_apps_config',
            'yoreni.agent.tools.filesystem_config',
            'yoreni.agent.tools.image_generation_config',
            'yoreni.agent.tools.self_config',
            'yoreni.agent.tools.shell_config',
            'yoreni.agent.tools.web_config',
        }
        if fullname in config_modules:
            return None
        forbidden = (
            'yoreni.agent', 'yoreni.api', 'yoreni.apps', 'yoreni.channels',
            'yoreni.cli', 'yoreni.session', 'yoreni.webui', 'yoreni.providers.base',
        )
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('configuration imported runtime implementation: ' + fullname)

sys.meta_path.insert(0, RuntimeImportBlocker())
from yoreni.config import Config
from yoreni.config.schema import ModelPresetConfig

config = Config.model_validate({'tools': {
    'exec': {'timeout': 37},
    'imageGeneration': {
        'enabled': True,
        'extraHeaders': {'X-Image-Mode': 'test'},
        'extraBody': {'quality': 'high', 'provider': {'order': ['example']}},
    },
}})
assert config.tools.exec.timeout == 37
restored = Config.model_validate_json(config.model_dump_json(by_alias=True))
assert restored.tools.exec.timeout == 37
assert restored.tools.image_generation.enabled is True
assert restored.tools.image_generation.extra_headers == {'X-Image-Mode': 'test'}
assert restored.tools.image_generation.extra_body == {
    'quality': 'high', 'provider': {'order': ['example']},
}
assert Config.model_json_schema()['properties']['tools']
assert ModelPresetConfig(model='example').to_generation_settings().max_tokens == 8192
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_public_tool_contracts_can_be_imported_without_agent_execution():
    code = """
import sys
from yoreni.agent.tools import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.base import Tool as BaseTool

assert Tool is BaseTool
assert callable(tool_parameters)
assert ToolResult
assert 'yoreni.agent.loop' not in sys.modules
assert 'yoreni.agent.runner' not in sys.modules
assert 'yoreni.agent.tools.shell' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("module", "names"),
    [
        ("cli_apps", ("CliAppsToolConfig",)),
        ("filesystem", ("FileToolsConfig",)),
        ("image_generation", ("ImageGenerationToolConfig",)),
        ("self", ("MyToolConfig",)),
        ("shell", ("ExecToolConfig",)),
        ("web", ("WebSearchConfig", "WebFetchConfig", "WebToolsConfig")),
    ],
)
def test_tool_config_imports_keep_the_same_public_class(module, names):
    contracts = import_module(f"yoreni.agent.tools.{module}_config")
    aggregate = import_module("yoreni.config.schema")
    implementation = import_module(f"yoreni.agent.tools.{module}")
    for name in names:
        assert getattr(implementation, name) is getattr(contracts, name)
        assert getattr(aggregate, name) is getattr(contracts, name)
