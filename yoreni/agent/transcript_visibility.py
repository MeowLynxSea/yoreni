"""Visibility of model-only transcript annotations."""
from collections.abc import Mapping
from typing import Any

HIDDEN_HISTORY_META = "_hidden_history"

def is_hidden_history_message(message: Mapping[str, Any] | None) -> bool:
    marker = message.get(HIDDEN_HISTORY_META) if message else None
    return marker is True or isinstance(marker, Mapping)
