"""Pairing module for DM sender approval."""

from yoreni.pairing.store import (
    approve_code,
    clear_channel,
    deny_code,
    format_expiry,
    format_pairing_reply,
    generate_code,
    get_approved,
    is_approved,
    list_pending,
    record_delivery,
    revoke,
    revoke_channel,
)

# Metadata key used by channels to tag pairing-related messages.
PAIRING_CODE_META_KEY = "_pairing_code"

__all__ = [
    "approve_code",
    "clear_channel",
    "deny_code",
    "format_expiry",
    "format_pairing_reply",
    "generate_code",
    "get_approved",
    "is_approved",
    "list_pending",
    "record_delivery",
    "revoke",
    "revoke_channel",
    "PAIRING_CODE_META_KEY",
]
