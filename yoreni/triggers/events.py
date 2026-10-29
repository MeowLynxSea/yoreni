"""Local trigger Event metadata."""

from collections.abc import Mapping
from typing import Any, cast

LOCAL_TRIGGER_META = "_local_trigger"


def local_trigger(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    value = (metadata or {}).get(LOCAL_TRIGGER_META)
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def local_trigger_delivery_id(metadata: Mapping[str, Any] | None) -> str | None:
    value = (local_trigger(metadata) or {}).get("delivery_id")
    return value if isinstance(value, str) and value else None
