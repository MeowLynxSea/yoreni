"""Shared MIME and batch limits for WebSocket attachment ingress."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentIngressLimits:
    max_count: int = 4
    max_file_bytes: int = 6 * 1024 * 1024
    max_total_bytes: int = 24 * 1024 * 1024


MAX_VIDEOS_PER_MESSAGE = 1
MAX_VIDEO_BYTES = 20 * 1024 * 1024

# A 20 MiB upload takes about 168 seconds at 1 Mbps. Keep a finite total
# budget with room for contention, but release stalled uploads promptly.
UPLOAD_TIMEOUT_SECONDS = 300.0
UPLOAD_IDLE_TIMEOUT_SECONDS = 30.0
UPLOAD_REQUEST_TIMEOUT_SECONDS = 315.0

IMAGE_MIMES: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
})
VIDEO_MIMES: frozenset[str] = frozenset({
    "video/mp4", "video/webm", "video/quicktime",
})

# Any syntactically valid ``type/subtype`` is accepted: uploads carry arbitrary
# files, and the sets above only classify media for size and count limits. The
# pattern bounds the MIME to a single token pair so it stays safe to use as a
# filename extension lookup and in stored metadata.
_MIME_RE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}")


def is_valid_mime(mime: str) -> bool:
    return _MIME_RE.fullmatch(mime) is not None


def validate_attachment_batch(
    mimes: list[str], *, limits: AttachmentIngressLimits,
) -> None:
    """Apply shared format and count limits without imposing an upload budget."""
    videos = sum(mime in VIDEO_MIMES for mime in mimes)
    if videos > MAX_VIDEOS_PER_MESSAGE or len(mimes) - videos > limits.max_count:
        raise ValueError("Too many attachments")
    if any(not is_valid_mime(mime) for mime in mimes):
        raise ValueError("Invalid attachment MIME")


def validate_upload_size(mime: str, size: int, *, limits: AttachmentIngressLimits) -> None:
    if isinstance(size, bool) or size <= 0:
        raise ValueError("Empty files cannot be uploaded")
    limit = MAX_VIDEO_BYTES if mime in VIDEO_MIMES else limits.max_file_bytes
    if size > limit:
        maximum = f"{limit / (1024 * 1024):g} MiB" if limit >= 1024 * 1024 else f"{limit:,} bytes"
        raise ValueError(f"Attachment exceeds the {maximum} upload limit ({size:,} bytes)")


def validate_upload_batch(
    items: list[tuple[str, int]], *, limits: AttachmentIngressLimits,
) -> None:
    """Upload ownership alone incurs per-file and cumulative byte limits."""
    validate_attachment_batch([mime for mime, _ in items], limits=limits)
    for mime, size in items:
        validate_upload_size(mime, size, limits=limits)
    total = sum(size for mime, size in items if mime not in VIDEO_MIMES)
    if total > limits.max_total_bytes:
        raise ValueError(f"Uploaded attachments exceed the total upload limit ({total:,} bytes; maximum {limits.max_total_bytes:,} bytes)")
