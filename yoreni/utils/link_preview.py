"""Server-side link previews.

A browser cannot read another origin's page title or meta description, so the
card that stands for a link is built here: one small HTML request per link,
parsed for the fields a conversation card needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from loguru import logger

from yoreni.security.network import PinnedDNSAsyncTransport, validate_url_target

_MAX_BYTES = 256 * 1024
_MAX_REDIRECTS = 4
_TIMEOUT_S = 6.0
_TITLE_LIMIT = 200
_DESCRIPTION_LIMIT = 300
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class LinkPreview:
    """What a chat card shows for an external link."""

    url: str
    title: str
    description: str = ""
    site: str = ""


def host_label(url: str) -> str:
    """Return the readable site name a card falls back to when nothing else is known."""
    try:
        host = (urlparse(url).hostname or "").rstrip(".")
    except ValueError:
        return url
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or url


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def link_payload(preview: LinkPreview) -> dict[str, str]:
    """Wire shape of a link card inside ``media_urls``."""
    return {
        "kind": "link",
        "url": preview.url,
        "name": preview.title,
        "title": preview.title,
        "description": preview.description,
        "site": preview.site,
    }


def fallback_preview(url: str) -> LinkPreview:
    site = host_label(url)
    return LinkPreview(url=url, title=site, site=site)


def _clean(value: str, limit: int) -> str:
    text = _SPACES.sub(" ", _CONTROL_CHARS.sub(" ", value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class _MetadataParser(HTMLParser):
    """Collect the handful of tags a card renders, stopping at the body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            data = {key.lower(): (value or "") for key, value in attrs}
            name = (data.get("property") or data.get("name") or "").strip().lower()
            content = data.get("content", "").strip()
            if name and content and name not in self.meta:
                self.meta[name] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._done and data.strip():
            self.title_parts.append(data.strip())


def preview_from_html(html: str, url: str) -> LinkPreview:
    """Read a card out of an HTML document, falling back to the host name."""
    parser = _MetadataParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # malformed markup is a normal web condition
        logger.debug("link preview parse failed for {}: {}", host_label(url), exc)
    title = _clean(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or " ".join(parser.title_parts)
        or "",
        _TITLE_LIMIT,
    )
    description = _clean(
        parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
        or parser.meta.get("description")
        or "",
        _DESCRIPTION_LIMIT,
    )
    site = _clean(parser.meta.get("og:site_name") or "", 80) or host_label(url)
    return LinkPreview(url=url, title=title or site, description=description, site=site)


async def _read_html(client: httpx.AsyncClient, url: str) -> str | None:
    """Follow redirects to an HTML document, validating each hop first."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        allowed, error = validate_url_target(current)
        if not allowed:
            logger.debug("link preview skipped {}: {}", host_label(current), error)
            return None
        async with client.stream("GET", current) as response:
            location = response.headers.get("location")
            if 300 <= response.status_code < 400 and location:
                current = urljoin(str(response.url), location)
                continue
            if response.status_code >= 400:
                return None
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                return None
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= _MAX_BYTES:
                    break
            charset = response.charset_encoding or "utf-8"
            return b"".join(chunks)[:_MAX_BYTES].decode(charset, "replace")
    return None


async def fetch_link_preview(url: str, *, timeout: float = _TIMEOUT_S) -> LinkPreview:
    """Fetch what a card needs for ``url``.

    Never raises and never returns empty fields: an unreachable, private, or
    non-HTML target still produces the host-name card, so delivery never
    depends on the remote site being up.
    """
    if not is_http_url(url):
        return fallback_preview(url)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=PinnedDNSAsyncTransport(),
            follow_redirects=False,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en;q=0.9,*;q=0.5",
            },
        ) as client:
            html = await _read_html(client, url)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.debug("link preview fetch failed for {}: {}", host_label(url), exc)
        return fallback_preview(url)
    if html is None:
        return fallback_preview(url)
    return preview_from_html(html, url)
