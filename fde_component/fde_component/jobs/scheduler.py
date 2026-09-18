"""
fde_component.jobs.scheduler
=============================
Incremental sync schedule enqueuer for SP-05.

Called by the cron hook in hooks.py every 5 minutes.  Sweeps active
SyncSchedule docs and enqueues a SyncJob for each one whose cron
expression fires now.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import frappe
from frappe.utils import cint, get_datetime, now_datetime

logger = logging.getLogger(__name__)


def enqueue_due_schedules() -> None:
    """
    Enqueue SyncJobs for all active SyncSchedule docs that are due.

    Each SyncSchedule carries:
    - ``cron`` : cron expression (5-field)
    - ``job_type`` : the CheckpointedJob subclass name
    - ``source_config`` : JSON dict passed to the job
    - ``last_fired`` : datetime of last enqueue (set after successful enqueue)

    Cron matching uses Frappe's ``crontab`` module.
    """
    if not frappe.db.exists("DocType", "SyncSchedule"):
        return  # DocType not installed yet

    now = now_datetime()
    schedules = frappe.get_all(
        "SyncSchedule",
        filters={"active": True},
        fields=["name", "job_type", "cron", "source_config", "last_fired"],
    )

    enqueued = 0
    for sched in schedules:
        if not _is_due(sched["cron"], sched["last_fired"], now):
            continue

        try:
            _enqueue_schedule(sched, now)
            enqueued += 1
        except Exception:
            logger.exception("Failed to enqueue schedule %s", sched["name"])
            frappe.log_error(
                title=f"Schedule enqueue failed: {sched['name']}",
                message=frappe.get_traceback(),
            )

    if enqueued > 0:
        logger.info("Enqueued %d sync jobs from schedules", enqueued)


def _is_due(cron: str, last_fired: str | None, now: datetime) -> bool:
    """
    Return True if *cron* fires between *last_fired* and *now*.
    Uses the ``croniter`` library (bundled with Frappe).
    """
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("croniter not available — skipping schedule check")
        return False

    if last_fired:
        prev = get_datetime(last_fired)
    else:
        prev = now  # never fired before → treat as due

    try:
        itr = croniter(cron, prev)
        nxt = itr.get_next(datetime)
        return nxt <= now
    except Exception:
        logger.exception("Invalid cron expression: %s", cron)
        return False


def _enqueue_schedule(sched: dict, now: datetime) -> None:
    """
    Create a SyncJob doc for *sched* and enqueue it via Frappe RQ.
    """
    source_config = {}
    if sched.get("source_config"):
        try:
            source_config = frappe.parse_json(sched["source_config"])
        except Exception:
            source_config = {}

    # Build job name
    job_name = f"sync-{sched['job_type']}-{int(now.timestamp())}"

    sync_job = frappe.new_doc("SyncJob")
    sync_job.job_type = sched["job_type"]
    sync_job.status = "Queued"
    sync_job.source_config = sched.get("source_config", "{}")
    sync_job.total_records = source_config.get("total_records")
    sync_job.insert(ignore_permissions=True)
    frappe.db.commit()

    # Enqueue via RQ — the worker picks it up
    frappe.enqueue(
        method="fde_component.jobs.runner.run_job",
        queue="default",
        job_id=sync_job.name,
        timeout=7200,  # 2 hours — override per job type if needed
        sync_job_name=sync_job.name,
        job_type=sched["job_type"],
    )

    # Update last_fired
    frappe.db.set_value("SyncSchedule", sched["name"], "last_fired", now)
    frappe.db.commit()

    logger.info("Enqueued SyncJob %s for schedule %s", sync_job.name, sched["name"])
