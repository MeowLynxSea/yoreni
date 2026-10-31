"""Turn multimodal tool results into durable text evidence and media files.

Tool messages carry a list of content blocks when they include images. Memory
keeps text evidence, so every image is either stored under the workspace media
directory and referenced from the evidence text, or explicitly dropped when
multimodal embeddings are disabled.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any, Sequence, cast

from loguru import logger

IMAGE_MARKER_PREFIX = "[image: "
IMAGE_MARKER_SUFFIX = "]"
IMAGE_OMITTED = "[image omitted]"
_MARKER_PATTERN = re.compile(r"\[image: ([^\]\n]+)\]")
_DATA_URL_PREFIX = "data:image/"
_MAX_IMAGE_BYTES = 12_000_000
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def image_marker(reference: str) -> str:
    """Render one evidence marker for an image path or URL."""
    return f"{IMAGE_MARKER_PREFIX}{reference}{IMAGE_MARKER_SUFFIX}"


def image_markers(content: str) -> list[str]:
    """Return image references recorded in evidence text, in order."""
    seen: dict[str, None] = {}
    for match in _MARKER_PATTERN.finditer(content):
        seen.setdefault(match.group(1).strip(), None)
    return [reference for reference in seen if reference]


def evidence_text_with_images(
    text: str,
    images: Sequence[str],
    *,
    multimodal: bool,
    directory: Path | None,
) -> str:
    """Append one marker per image to evidence text.

    With multimodal embeddings the image is persisted and referenced by path so
    later embedding can read it back; otherwise the image is explicitly
    dropped and the marker only records that it existed.
    """
    if not images:
        return text
    markers: list[str] = []
    if multimodal and directory is not None:
        markers = [
            image_marker(reference)
            for reference in (store_tool_image(url, directory) for url in images)
            if reference
        ]
        markers.extend([IMAGE_OMITTED] * (len(images) - len(markers)))
    elif not text.strip():
        # Without multimodal embeddings an image-only result would otherwise
        # disappear; keep one explicit marker so the evidence still exists.
        markers = [IMAGE_OMITTED] * len(images)
    return "\n".join(part for part in [text, *markers] if part)


def split_tool_content(content: str | Sequence[Any]) -> tuple[str, list[str]]:
    """Split a tool result into its text and inline image payloads."""
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[str] = []
    for raw_block in content:
        if not isinstance(raw_block, dict):
            continue
        block = cast(dict[str, Any], raw_block)
        block_type = block.get("type")
        if block_type in {"text", "input_text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
        elif block_type in {"image_url", "input_image"}:
            url = _image_url(block)
            if url:
                images.append(url)
    return "\n".join(texts).strip(), images


def store_tool_image(url: str, directory: Path) -> str | None:
    """Persist one inline image under ``directory`` and return its reference.

    Remote URLs stay remote; only inline base64 payloads become files. A
    payload that cannot be decoded is dropped instead of failing the Run.
    """
    if not url.startswith(_DATA_URL_PREFIX):
        return url if url.startswith(("http://", "https://")) else None
    header, _, payload = url.partition(",")
    if ";base64" not in header:
        return None
    mime = header[len("data:"):].split(";", 1)[0].lower()
    if mime not in _MIME_EXTENSIONS:
        return None
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        logger.debug("memory evidence image payload is not valid base64")
        return None
    if not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None
    digest = hashlib.sha256(raw).hexdigest()[:32]
    path = directory / f"{digest}{_MIME_EXTENSIONS[mime]}"
    if not path.exists():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        except OSError:
            logger.warning("memory evidence image could not be written to {}", path)
            return None
    return str(path)


def image_data_url(reference: str) -> str | None:
    """Return a data URL for a stored image reference, if it is a local file."""
    path = Path(reference)
    if not path.is_absolute():
        return reference if reference.startswith(("http://", "https://", "data:image/")) else None
    if not path.is_file():
        return None
    mime = next((value for value, ext in _MIME_EXTENSIONS.items() if path.suffix == ext), None)
    if mime is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _image_url(block: dict[str, Any]) -> str | None:
    container = block.get("image_url")
    if isinstance(container, str):
        return container or None
    if isinstance(container, dict):
        url = cast(dict[str, Any], container).get("url")
        return url if isinstance(url, str) and url else None
    return None


__all__ = [
    "IMAGE_OMITTED",
    "evidence_text_with_images",
    "image_data_url",
    "image_marker",
    "image_markers",
    "split_tool_content",
    "store_tool_image",
]
