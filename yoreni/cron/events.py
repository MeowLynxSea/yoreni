"""Authoritative cron Event metadata."""

from collections.abc import Mapping
from typing import Any, cast

from yoreni.cron.types import CronJob

CRON_TRIGGER_META = "_cron_trigger"


def cron_trigger(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    value = (metadata or {}).get(CRON_TRIGGER_META)
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def is_cron_turn(metadata: Mapping[str, Any] | None) -> bool:
    return cron_trigger(metadata) is not None


def cron_run_id(metadata: Mapping[str, Any] | None) -> str | None:
    value = (cron_trigger(metadata) or {}).get("run_id")
    return value if isinstance(value, str) and value else None


def is_bound_cron_job(job: CronJob) -> bool:
    return bool(job.payload.kind == "agent_turn" and job.payload.agent_id
                and job.payload.origin_channel and job.payload.origin_chat_id)
