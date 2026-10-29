"""Execution helpers for Agent-owned cron jobs."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, Protocol

from yoreni.bus.delivery import DELIVERY_RUN_META
from yoreni.bus.events import InboundMessage, OutboundMessage
from yoreni.cron.delivery import origin_delivery_context
from yoreni.cron.events import CRON_TRIGGER_META
from yoreni.cron.types import CronJob, CronRunResult
from yoreni.cron.webui_metadata import cron_proactive_delivery_metadata
from yoreni.utils.prompt_templates import render_template


class BoundCronAgent(Protocol):
    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        ...


class CronRunRecorder(Protocol):
    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        ...


def _cron_prompt_ref(prompt: str) -> dict[str, Any]:
    return {
        "id": "cron.agent_turn.reminder",
        "version": 1,
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }


def _origin_delivery_context(
    job: CronJob,
    *,
    turn_seed: str,
    source_label: str | None,
) -> tuple[str, str, dict[str, Any]]:
    channel, chat_id, metadata = origin_delivery_context(job)

    if channel == "websocket":
        metadata["webui"] = True
        metadata.update(
            cron_proactive_delivery_metadata(
                "websocket",
                metadata,
                turn_seed=turn_seed,
                source_label=source_label,
            )
        )
    else:
        # External scheduled results need one recoverable final message, not a
        # transient stream whose completion marker cannot be replayed safely.
        metadata.pop("_wants_stream", None)

    return channel, chat_id, metadata


async def run_bound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> CronRunResult:
    """Execute a Agent-owned cron job as an internal Event."""
    agent_id = job.payload.agent_id
    if not agent_id:
        raise ValueError(f"cron job {job.id} is missing payload.agent_id")

    prompt = render_template(
        "agent/cron_reminder.md",
        strip=True,
        message=job.payload.message,
    )
    prompt_ref = _cron_prompt_ref(prompt)
    run_id = f"{job.id}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"
    channel, chat_id, metadata = _origin_delivery_context(
        job,
        turn_seed=f"cron:{job.id}",
        source_label=job.name,
    )
    metadata[CRON_TRIGGER_META] = {
        "job_id": job.id,
        "job_name": job.name,
        "agent_id": agent_id,
        "run_id": run_id,
        "prompt_ref": prompt_ref,
        "persist_content": (
            f"Scheduled cron job triggered: {job.name}\n\n{job.payload.message}"
        ),
    }
    metadata[DELIVERY_RUN_META] = run_id
    run_record_base: dict[str, Any] = {
        "job_id": job.id,
        "job_name": job.name,
        "agent_id": agent_id,
        "prompt_ref": prompt_ref,
        "prompt_vars": {"message": job.payload.message},
        "rendered_prompt": prompt,
    }

    cron.write_run_record(
        run_id,
        {
            **run_record_base,
            "status": "queued",
        },
    )

    try:
        resp = await agent.submit_cron_turn(
            InboundMessage(
                channel=channel,
                sender_id="cron",
                chat_id=chat_id,
                content=prompt,
                metadata=metadata, input_role="system",

            )
        )
    except (Exception, asyncio.CancelledError) as exc:
        error_text = str(exc) or exc.__class__.__name__
        cron.write_run_record(
            run_id,
            {
                **run_record_base,
                "status": "error",
                "error": error_text,
            },
        )
        raise
    response = resp.content if resp else ""
    cron.write_run_record(
        run_id,
        {
            **run_record_base,
            "status": "ok",
            "response": response,
        },
    )
    return CronRunResult(run_id=run_id, response=response)
