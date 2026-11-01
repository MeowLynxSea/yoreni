"""Workspace-scoped media references for live output."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_MARKDOWN_LOCAL_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\((<[^>]+>|[^)\s]+)(\s+(?:\"[^\"]*\"|'[^']*'))?\)"
)
_INLINE_MARKDOWN_IMAGE_EXTS: frozenset[str] = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
})
_INLINE_MARKDOWN_VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4",
    ".mov",
    ".webm",
})
_INLINE_MARKDOWN_MEDIA_EXTS = _INLINE_MARKDOWN_IMAGE_EXTS | _INLINE_MARKDOWN_VIDEO_EXTS


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return False
    return True


async def rewrite_local_markdown_images(
    text: str,
    *,
    workspace_path: Path,
    sign_path: Callable[[Path], Awaitable[Mapping[str, Any] | None]],
    machine_workspace: Path | None = None,
) -> str:
    """Rewrite markdown media paths inside the workspace to signed WebUI media URLs.

    Paths are accepted from the app workspace and from the agent's own machine
    workspace; signing decides whether the bytes exist and can be served.
    """
    if "![" not in text:
        return text

    async def sign_url(raw_url: str) -> str | None:
        url = raw_url.strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1].strip()
        if not url or url.startswith(("/api/media/", "#")):
            return None
        parsed = urlparse(url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
        path_text = unquote(url)
        if Path(path_text).suffix.lower() not in _INLINE_MARKDOWN_MEDIA_EXTS:
            return None
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_path / candidate
        if not _inside(candidate, workspace_path) and not (
            machine_workspace is not None and _inside(candidate, machine_workspace)
        ):
            return None
        signed = await sign_path(candidate)
        return str(signed.get("url")) if signed and signed.get("url") else None

    signed_urls: dict[str, str | None] = {}
    for match in _MARKDOWN_LOCAL_IMAGE_RE.finditer(text):
        raw_url = match.group(2)
        if raw_url not in signed_urls:
            signed_urls[raw_url] = await sign_url(raw_url)

    def replace(match: re.Match[str]) -> str:
        signed_url = signed_urls.get(match.group(2))
        if not signed_url:
            return match.group(0)
        title = match.group(3) or ""
        return f"![{match.group(1)}]({signed_url}{title})"

    return _MARKDOWN_LOCAL_IMAGE_RE.sub(replace, text)
