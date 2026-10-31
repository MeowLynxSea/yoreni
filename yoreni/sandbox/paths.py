"""Lexical path policy for the VM filesystem.

Paths here are POSIX paths inside the VM. They are never resolved against the
host filesystem, because the host may not even share the same directory
layout. Symlink containment is re-checked by the gateway with a ``realpath``
call inside the VM before a file operation runs.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

from yoreni.sandbox.errors import SandboxPathError


def normalize_vm_path(path: str, *, cwd: str = "/") -> str:
    """Return one absolute, normalized POSIX path for the VM."""
    raw = path.strip()
    if not raw:
        raise SandboxPathError("path must not be empty")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise SandboxPathError("path contains invalid characters")
    if "\\" in raw:
        raise SandboxPathError("the sandbox uses POSIX paths; use '/' separators")
    candidate = raw if raw.startswith("/") else posixpath.join(cwd, raw)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        raise SandboxPathError("path must resolve to an absolute VM path")
    return normalized


def is_within(path: str, root: str) -> bool:
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


@dataclass(frozen=True)
class PathRoots:
    """The VM roots an operation may touch."""

    read: tuple[str, ...]
    write: tuple[str, ...]

    def check_read(self, path: str) -> None:
        if not any(is_within(path, root) for root in self.read):
            raise SandboxPathError(
                "path is outside the sandbox workspace; use the import tools for host files"
            )

    def check_write(self, path: str) -> None:
        if not any(is_within(path, root) for root in self.write):
            raise SandboxPathError(
                "path is outside the sandbox workspace and cannot be modified"
            )

    @classmethod
    def for_workspace(
        cls,
        workspace: str,
        *,
        extra_read: Iterable[str] = (),
        extra_write: Iterable[str] = (),
    ) -> "PathRoots":
        return cls(
            read=tuple(dict.fromkeys((workspace, *extra_read))),
            write=tuple(dict.fromkeys((workspace, *extra_write))),
        )
