"""The persistent virtual machine that owns every agent execution surface.

The sandbox package is the execution plane. File tools, shell tools, the
browser and the desktop all resolve through :class:`SandboxGateway`; none of
them may fall back to the host. The module deliberately has no import of the
agent runtime so the gateway can be composed before tools are registered.
"""

from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import (
    SandboxControlError,
    SandboxError,
    SandboxUnavailableError,
)
from yoreni.sandbox.gateway import SandboxGateway, get_sandbox_gateway, set_sandbox_gateway
from yoreni.sandbox.models import (
    ControlOwner,
    ControlSnapshot,
    ExecResult,
    SandboxHealth,
    SandboxStatus,
)

__all__ = [
    "ControlOwner",
    "ControlSnapshot",
    "ExecResult",
    "SandboxConfig",
    "SandboxControlError",
    "SandboxError",
    "SandboxGateway",
    "SandboxHealth",
    "SandboxStatus",
    "SandboxUnavailableError",
    "get_sandbox_gateway",
    "set_sandbox_gateway",
]
