"""Best-effort layer sizes of an OCI image, used to report the first image pull.

The registry is only consulted for progress: any failure is silent and the
caller falls back to an indeterminate stage.
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
_CACHE: dict[str, dict[str, int] | None] = {}
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


def _layer_sizes(payload: dict[str, Any]) -> dict[str, int] | None:
    layers = payload.get("layers")
    if not isinstance(layers, list):
        return None
    sizes: dict[str, int] = {}
    for layer in cast(list[Any], layers):
        if not isinstance(layer, dict):
            return None
        digest = cast(dict[str, Any], layer).get("digest")
        size = cast(dict[str, Any], layer).get("size")
        if not isinstance(digest, str) or not isinstance(size, int):
            return None
        # The runtime names a downloaded layer after its bare hex digest.
        sizes[digest.removeprefix("sha256:")] = size
    return sizes or None


def image_layer_sizes(image: str) -> dict[str, int] | None:
    """Return the compressed size of each layer of ``image``, if known.

    The digests let the caller count an in-flight download against the layers
    that actually belong to this image. Only a resolved answer is cached: the
    lookup runs when a pull begins, which can be right after a restart before
    the network is back, and caching that miss would leave the download without
    a total for the rest of the process.
    """
    with _LOCK:
        if image in _CACHE:
            return _CACHE[image]
    sizes = _fetch(image)
    if sizes is not None:
        with _LOCK:
            _CACHE[image] = sizes
    return sizes


def _fetch(image: str) -> dict[str, int] | None:
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
            return _layer_sizes(payload)
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
