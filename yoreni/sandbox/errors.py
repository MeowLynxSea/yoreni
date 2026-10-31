"""Errors raised by the execution plane."""

from __future__ import annotations


class SandboxError(RuntimeError):
    """Base class for execution-plane failures."""


class SandboxUnavailableError(SandboxError):
    """The VM cannot be reached; no host fallback exists."""


class SandboxControlError(SandboxError):
    """A control request conflicts with the current owner."""


class SandboxPathError(SandboxError):
    """A path is outside the VM workspace or its controlled import roots."""


class SandboxNotReadyError(SandboxUnavailableError):
    """The VM exists but has not finished starting or provisioning."""
