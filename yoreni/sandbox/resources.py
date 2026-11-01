"""The CPU and memory budget the sandbox VM may take from this host.

A box's CPU count and memory are creation options: boxlite ignores them when it
reuses an existing box and has no resize call, so the values have to be chosen
before the first VM exists. Onboarding asks for them; settings read them again
when the owner decides to rebuild.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.errors import SandboxUnavailableError

# Below this the browser and the window manager thrash on a 2-core guest.
MIN_MEMORY_MIB = 2048
# boxlite's own ceiling for a box's CPU count.
_MAX_CPUS = 32
_MIB = 1024 * 1024


@dataclass(frozen=True)
class VmLimits:
    """What this host is willing to give away."""

    cpus_max: int
    memory_mib_max: int


def vm_limits() -> VmLimits:
    """Return the largest VM size this host can carry."""
    cpus_max = max(1, min(os.cpu_count() or 1, _MAX_CPUS))
    total_mib = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // _MIB
    # The gateway, the model calls and the desktop's host processes all run
    # beside the VM, so the VM never gets the whole machine.
    usable = max(MIN_MEMORY_MIB, total_mib - min(2048, total_mib // 4))
    return VmLimits(cpus_max=cpus_max, memory_mib_max=usable // 1024 * 1024)


def read_resources(config: SandboxConfig) -> dict[str, Any]:
    """Describe the configured VM size together with the host's limits."""
    limits = vm_limits()
    return {
        "cpus": config.cpus,
        "memory_mib": config.memory_mib,
        "cpus_max": limits.cpus_max,
        "memory_mib_max": limits.memory_mib_max,
    }


def write_resources(
    update_config: Callable[[Callable[[Any], None]], Any],
    cpus: Any,
    memory_mib: Any,
) -> dict[str, Any]:
    """Persist a size and apply it to the running gateway before the VM exists.

    A VM that already exists keeps its size: boxlite reads these options only
    when it creates a box, which is why the caller has to rebuild to apply them.
    """
    limits = vm_limits()
    chosen_cpus = _as_int(cpus, "CPU cores")
    chosen_memory = _as_int(memory_mib, "memory")
    if not 1 <= chosen_cpus <= limits.cpus_max:
        raise ValueError(f"Choose between 1 and {limits.cpus_max} CPU cores")
    if not MIN_MEMORY_MIB <= chosen_memory <= limits.memory_mib_max:
        raise ValueError(
            f"Choose between {MIN_MEMORY_MIB // 1024} and "
            f"{limits.memory_mib_max // 1024} GB of memory"
        )

    def mutate(config: Any) -> None:
        config.tools.sandbox.cpus = chosen_cpus
        config.tools.sandbox.memory_mib = chosen_memory

    update_config(mutate)
    gateway = _live_gateway()
    if gateway is not None:
        # The backend holds this same object, so a VM created later reads it.
        gateway.config.cpus = chosen_cpus
        gateway.config.memory_mib = chosen_memory
    return {
        "cpus": chosen_cpus,
        "memory_mib": chosen_memory,
        "cpus_max": limits.cpus_max,
        "memory_mib_max": limits.memory_mib_max,
    }


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"The {label} setting must be a number")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"The {label} setting must be a number") from None
    if number != float(value):
        raise ValueError(f"The {label} setting must be a whole number")
    return number


def _live_gateway() -> Any:
    from yoreni.sandbox.gateway import get_sandbox_gateway

    try:
        return get_sandbox_gateway()
    except SandboxUnavailableError:
        return None
