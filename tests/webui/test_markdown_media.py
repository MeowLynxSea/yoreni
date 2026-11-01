"""Markdown images that live on the agent's machine still reach the WebUI."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yoreni.webui.media_gateway import WebUIMediaGateway


def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exported: Path | None):
    class _Machine:
        config = SimpleNamespace(workspace="/workspace")

        async def materialize_media(self, path: str, *, subdir: str = "outbound") -> str | None:
            assert path == "/workspace/chart.png"
            return str(exported) if exported is not None else None

    monkeypatch.setattr("yoreni.sandbox.gateway.get_sandbox_gateway", lambda: _Machine())
    return WebUIMediaGateway(
        workspace_path=tmp_path,
        logger=SimpleNamespace(warning=lambda *a, **k: None),
        media_dir=lambda channel: (tmp_path / "media"),
    )


@pytest.mark.asyncio
async def test_markdown_image_is_signed_from_the_agent_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "media").mkdir()
    exported = tmp_path / "chart.png"
    exported.write_bytes(b"png-bytes")
    media = _gateway(tmp_path, monkeypatch, exported=exported)

    text = await media.rewrite_local_markdown_images("chart: ![trend](/workspace/chart.png)")

    assert "/api/media/" in text
    assert "/workspace/chart.png" not in text


@pytest.mark.asyncio
async def test_markdown_image_outside_both_workspaces_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "media").mkdir()
    media = _gateway(tmp_path, monkeypatch, exported=None)

    text = await media.rewrite_local_markdown_images("secret ![x](/etc/shadow.png)")

    assert text == "secret ![x](/etc/shadow.png)"
