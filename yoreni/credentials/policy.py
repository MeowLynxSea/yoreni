"""Rules deciding where a placeholder may be turned back into a secret.

Restoration is the most dangerous operation in the credential system: it turns
model-authored text into a real secret that a tool will act on. The default is
denial. A call is allowed only when the tool is explicitly bound to the account,
the argument field is a credential-shaped input, and (for anything that reaches
the network) the destination matches the account's allowed domains.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, cast
from urllib.parse import urlsplit

from yoreni.credentials.vault import SecretEntry, SecretVault

# Tools that can move data anywhere on their own. A placeholder in these never
# restores, regardless of account bindings.
DENY_PREFIXES: tuple[str, ...] = (
    "shell",
    "exec",
    "terminal",
    "web_fetch",
    "web_search",
    "http",
    "fetch",
    "send_message",
    "write_file",
    "apply_patch",
    "memory",
    "cron",
    "widget",
)
DENY_TOOLS: frozenset[str] = frozenset({"read_file", "list_dir", "edit_file"})

# A placeholder must sit in a field whose purpose is to receive a credential.
ALLOWED_FIELDS: frozenset[str] = frozenset({
    "text",
    "value",
    "input",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "username",
    "user",
    "email",
    "code",
    "otp",
    "credential",
    "fill",
})

# Argument names that describe the page or service a call targets.
DOMAIN_PARAMS: tuple[str, ...] = (
    "url",
    "page_url",
    "origin",
    "host",
    "domain",
    "target_url",
    "base_url",
)

# A credential-bearing call must name a destination the account allows. There
# is no trustworthy way to infer the page a browser tool is already showing, so
# an omitted URL is a denial rather than an implicit wildcard.


class CredentialUseDeniedError(RuntimeError):
    """Raised when a tool call attempts credential use it is not allowed."""


def is_tool_denied(tool_name: str) -> bool:
    if tool_name in DENY_TOOLS:
        return True
    return any(tool_name == prefix or tool_name.startswith(prefix + "_") for prefix in DENY_PREFIXES)


def _tool_bound(tool_name: str, entry: SecretEntry) -> bool:
    return any(
        pattern == tool_name or fnmatchcase(tool_name, pattern)
        for pattern in entry.allowed_tools
    )


def _host_from_params(params: dict[str, Any]) -> str | None:
    for key in DOMAIN_PARAMS:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            raw = value.strip()
            parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
            if parsed.hostname:
                return parsed.hostname.lower()
    return None


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    for domain in domains:
        candidate = domain.strip().lower().lstrip(".")
        if candidate and (host == candidate or host.endswith("." + candidate)):
            return True
    return False


def _field_allowed(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].rsplit("[", 1)[0].strip().lower()
    return leaf in ALLOWED_FIELDS


def _walk(value: object, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        for key, item in mapping.items():
            child = f"{path}.{key}" if path else str(key)
            found.extend(_walk(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(cast(list[object] | tuple[object, ...], value)):
            found.extend(_walk(item, f"{path}[{index}]"))
    return found


def restore_tool_arguments(
    tool_name: str,
    params: dict[str, Any],
    vault: SecretVault,
    *,
    run_id: str | None,
) -> dict[str, Any]:
    """Return arguments with placeholders restored, or raise on denial."""
    _ = run_id
    placeholders = [
        (path, value, vault.placeholders_in(value))
        for path, value in _walk(params)
        if vault.placeholders_in(value)
    ]
    if not placeholders:
        return params

    if is_tool_denied(tool_name):
        raise CredentialUseDeniedError(
            f"{tool_name} cannot receive credentials; use a bound integration tool"
        )

    host = _host_from_params(params)
    for path, _value, tokens in placeholders:
        if not _field_allowed(path):
            raise CredentialUseDeniedError(
                f"credential placeholder is not allowed in argument '{path}'"
            )
        for token in tokens:
            entry = vault.entry_for_token(token)
            if entry is None:
                raise CredentialUseDeniedError("unknown credential placeholder")
            if not entry.restorable or not _tool_bound(tool_name, entry):
                raise CredentialUseDeniedError(
                    f"account {entry.account_id} is not bound to {tool_name}"
                )
            # Each credential must justify this destination on its own; one
            # account may not lend its domain list to another account. Without
            # an explicit target there is nothing to verify, so restore is
            # refused instead of assuming the current page is acceptable.
            if not entry.allowed_domains:
                raise CredentialUseDeniedError(
                    f"account {entry.account_id} has no allowed destination"
                )
            if host is None:
                raise CredentialUseDeniedError(
                    f"cannot verify the destination for account {entry.account_id}"
                )
            if not _host_allowed(host, entry.allowed_domains):
                raise CredentialUseDeniedError(
                    f"account {entry.account_id} is not allowed for this destination"
                )

    return cast(dict[str, Any], _restore(params, vault))


def _restore(value: object, vault: SecretVault) -> object:
    if isinstance(value, str):
        for token in vault.placeholders_in(value):
            entry = vault.entry_for_token(token)
            if entry is not None:
                value = value.replace(token, entry.secret)
        return value
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {key: _restore(item, vault) for key, item in mapping.items()}
    if isinstance(value, list):
        return [_restore(item, vault) for item in cast(list[object], value)]
    return value
