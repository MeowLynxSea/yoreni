"""Runtime log visibility controls shared by CLI commands."""

from loguru import logger

__all__ = ["_set_yoreni_logs"]


def _set_yoreni_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("yoreni")
    else:
        logger.disable("yoreni")
