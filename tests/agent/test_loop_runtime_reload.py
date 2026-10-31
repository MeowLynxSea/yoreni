from pathlib import Path
from unittest.mock import MagicMock

from yoreni.agent.loop import AgentLoop
from yoreni.bus.queue import MessageBus
from yoreni.config.schema import Config


def test_reload_runtime_applies_timezone_limits_tools_and_delivery(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        timezone="UTC",
    )
    callback = MagicMock()
    loop.set_delivery_callback(callback)

    config = Config()
    config.agents.defaults.timezone = "Asia/Tokyo"
    config.agents.defaults.max_tool_iterations = 7
    config.tools.exec.enable = False

    loop.reload_runtime(config)

    assert loop.context.timezone == "Asia/Tokyo"
    assert loop.max_iterations == 7
    assert loop.tools.get("exec") is None
    send_message = loop.tools.get("send_message")
    assert send_message is not None
    assert send_message._send_callback is callback
