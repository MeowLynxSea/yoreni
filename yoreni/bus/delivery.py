"""Delivery correlation and pre-send failure contracts, independent of transports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DELIVERY_RUN_META = "_delivery_run_id"


class ChannelNotReadyError(RuntimeError):
    """No part of the message was sent; retry after the channel reconnects."""


class DeliveryRejectedError(RuntimeError):
    """No part of the message was sent; configuration or destination needs repair."""


@dataclass(frozen=True)
class DeliveryRequest:
    """A scheduled result belongs to one run and its triggering Event."""

    run_id: str

    @classmethod
    def from_context(
        cls, metadata: Mapping[str, object],
    ) -> DeliveryRequest | None:
        run_id = metadata.get(DELIVERY_RUN_META)
        if not isinstance(run_id, str) or not run_id:
            return None
        return cls(run_id)
