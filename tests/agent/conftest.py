"""Shared fixtures and helpers for agent tests."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yoreni.agent.loop import AgentLoop
from yoreni.bus.queue import MessageBus
from yoreni.providers.base import LLMProvider


@pytest.fixture
def cmd_python() -> str:
    """Return the Python command name available to ExecTool tests."""
    return "python" if os.name == "nt" else "python3"


def make_provider(
    default_model: str = "test-model",
    *,
    max_tokens: int = 4096,
    spec: bool = True,
) -> MagicMock:
    """Create a spec-limited LLM provider mock."""
    mock_type = MagicMock(spec=LLMProvider) if spec else MagicMock()
    provider = mock_type
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    provider.estimate_prompt_tokens = MagicMock(return_value=(10_000, "test"))
    return provider


def make_loop(
    tmp_path: Path,
    *,
    model: str = "test-model",
    context_window_tokens: int = 128_000,
    tools_config=None,
    model_presets: dict | None = None,
    hooks: list | None = None,
    provider: MagicMock | None = None,
) -> AgentLoop:
    """Create a real AgentLoop for testing."""
    bus = MessageBus()
    if provider is None:
        provider = make_provider(default_model=model)

    kwargs = dict(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model=model,
        context_window_tokens=context_window_tokens,
    )
    if tools_config is not None:
        kwargs["tools_config"] = tools_config
    if model_presets is not None:
        kwargs["model_presets"] = model_presets
    if hooks is not None:
        kwargs["hooks"] = hooks

    return AgentLoop(**kwargs)


@pytest.fixture
def loop_factory(tmp_path):
    """Fixture providing a factory for creating AgentLoop instances."""
    def _factory(**kwargs):
        return make_loop(tmp_path, **kwargs)
    return _factory
