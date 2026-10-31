"""Shared fixtures for project tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from project_helpers import ProjectEnv, build_env


@pytest.fixture
def env(tmp_path: Path) -> ProjectEnv:
    return build_env(tmp_path)
