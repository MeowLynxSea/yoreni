"""Agent-owned task metadata for the automation manager."""

from dataclasses import asdict
from typing import Any

from yoreni.cron.service import CronService
from yoreni.triggers.local_store import LocalTriggerStore


def all_automations_payload(
    cron_service: CronService | None, *, local_trigger_store: LocalTriggerStore | None = None,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    for job in cron_service.list_jobs(include_disabled=True) if cron_service else ():
        jobs.append({**asdict(job), "kind": "cron", "pending": False,
                     "system": job.payload.kind == "system_event",
                     "origin": {"channel": job.payload.origin_channel,
                                "chat_id": job.payload.origin_chat_id}})
    for trigger in local_trigger_store.list_triggers(include_disabled=True) if local_trigger_store else ():
        jobs.append({**asdict(trigger), "kind": "local_trigger", "pending": False,
                     "system": False, "command": f'yoreni trigger {trigger.id} "message"'})
    return {"jobs": jobs}
