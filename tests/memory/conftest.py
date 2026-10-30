"""Shared fixtures for long-term memory tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from memory_helpers import MemoryHarness, ScriptedProvider

from yoreni.config.schema import MemoryConfig
from yoreni.memory.service import MemoryService
from yoreni.utils.llm_runtime import LLMRuntime


@pytest.fixture
def memory_config() -> MemoryConfig:
    return MemoryConfig()


@pytest.fixture
def harness(tmp_path: Path, memory_config: MemoryConfig) -> MemoryHarness:
    return MemoryHarness(MemoryService(tmp_path, memory_config), tmp_path)


@pytest.fixture
def runtime() -> LLMRuntime:
    return LLMRuntime.capture(
        ScriptedProvider(), "test-model", context_window_tokens=128_000,
    )
