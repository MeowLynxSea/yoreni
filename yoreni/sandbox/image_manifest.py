"""Best-effort size of an OCI image, used to estimate the first image pull.

The registry is only consulted for a progress total: any failure is silent and
the caller falls back to an indeterminate stage.
"""

from __future__ import annotations

import platform
import re
import threading
from typing import Any, cast

import httpx
from loguru import logger

_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_CACHE: dict[str, int | None] = {}
_LOCK = threading.Lock()
_TIMEOUT = 6.0


def _split_reference(image: str) -> tuple[str, str, str]:
    name, _, tag = image.partition(":")
    host, _, repository = name.partition("/")
    if not repository:
        host, repository = "docker.io", name
        if not repository.startswith("library/"):
            repository = f"library/{repository}"
    return host, repository, tag or "latest"


def _token(client: httpx.Client, challenge: str, repository: str) -> str | None:
    realm = re.search(r'realm="([^"]+)"', challenge)
    if realm is None:
        return None
    params: dict[str, str] = {}
    for key in ("service", "scope"):
        match = re.search(rf'{key}="([^"]+)"', challenge)
        if match:
            params[key] = match.group(1)
    params.setdefault("scope", f"repository:{repository}:pull")
    try:
        response = client.get(realm.group(1), params=params)
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
    except (httpx.HTTPError, ValueError):
        return None
    token = payload.get("token") or payload.get("access_token")
    return token if isinstance(token, str) else None


def _total_for_manifest(payload: dict[str, Any]) -> int | None:
    layers = payload.get("layers")
    if not isinstance(layers, list):
        return None
    total = 0
    for layer in cast(list[Any], layers):
        size = cast(dict[str, Any], layer).get("size") if isinstance(layer, dict) else None
        if not isinstance(size, int):
            return None
        total += size
    return total or None


def image_pull_bytes(image: str) -> int | None:
    """Return the compressed size of ``image`` for this machine, if known."""
    with _LOCK:
        if image in _CACHE:
            return _CACHE[image]
    total = _fetch(image)
    with _LOCK:
        _CACHE[image] = total
    return total


def _fetch(image: str) -> int | None:
    host, repository, tag = _split_reference(image)
    scheme = "https"
    base = f"{scheme}://{host}/v2/{repository}"
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            token: str | None = None
            response = client.get(f"{base}/manifests/{tag}", headers={"Accept": _ACCEPT})
            if response.status_code == 401:
                challenge = response.headers.get("www-authenticate", "")
                token = _token(client, challenge, repository)
                if token is None:
                    return None
                response = client.get(
                    f"{base}/manifests/{tag}",
                    headers={"Accept": _ACCEPT, "Authorization": f"Bearer {token}"},
                )
            response.raise_for_status()
            payload = cast(dict[str, Any], response.json())
            if "manifests" in payload:
                digest = _platform_digest(cast(list[Any], payload["manifests"]))
                if digest is None:
                    return None
                response = client.get(
                    f"{base}/manifests/{digest}",
                    headers={"Accept": _ACCEPT, "Authorization": f"Bearer {token}"}
                    if token
                    else {"Accept": _ACCEPT},
                )
                response.raise_for_status()
                payload = cast(dict[str, Any], response.json())
            return _total_for_manifest(payload)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.debug("Could not read the size of {}: {}", image, exc)
        return None


def _platform_digest(manifests: list[Any]) -> str | None:
    wanted = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(
        platform.machine().lower()
    )
    for entry in manifests:
        if not isinstance(entry, dict):
            continue
        item = cast(dict[str, Any], entry)
        details = item.get("platform")
        if not isinstance(details, dict):
            continue
        architecture = cast(dict[str, Any], details).get("architecture")
        os_name = cast(dict[str, Any], details).get("os")
        if architecture == wanted and os_name == "linux":
            digest = item.get("digest")
            return digest if isinstance(digest, str) else None
    return None
