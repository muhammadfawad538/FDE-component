"""
fde_component.jobs.recovery
=============================
Crash recovery for SP-05.

A worker killed with SIGKILL leaves its SyncJob in
"Running" with no signal handler to mark it
Interrupted, so runner.run_job() refuses to re-dispatch
it (it only claims Queued/Interrupted) and resume_job()
refuses to resume it (it only accepts Interrupted).

The recovery sweep moves a Running job whose newest
checkpoint is older than stale_after_minutes back to
Interrupted, so resume_job() can pick it up from the
last checkpoint.

Called by the cron hook every 5 minutes.
"""

from __future__ import annotations

import logging

import frappe
from frappe.utils import add_to_date, now_datetime

logger = logging.getLogger(__name__)

# Default: a Running job with no checkpoint for 10
# minutes is considered crashed. Override per site via
# the frappe.conf key "sync_job_stale_after_minutes".
DEFAULT_STALE_AFTER_MINUTES = 10


def _stale_after_minutes() -> int:
    """Read the threshold from frappe.conf, defaulting to
    DEFAULT_STALE_AFTER_MINUTES."""
    try:
        value = frappe.conf.get("sync_job_stale_after_minutes")
        if value is None:
            return DEFAULT_STALE_AFTER_MINUTES
        return int(value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid sync_job_stale_after_minutes=%r — using default %d",
            frappe.conf.get("sync_job_stale_after_minutes"),
            DEFAULT_STALE_AFTER_MINUTES,
        )
        return DEFAULT_STALE_AFTER_MINUTES


def recover_stalled_jobs() -> int:
    """
    Move Running SyncJobs with no checkpoint for
    stale_after_minutes back to Interrupted.

    Returns the number of jobs recovered.
    """
    stale_after = _stale_after_minutes()
    cutoff = add_to_date(now_datetime(), minutes=-stale_after)

    # A job is stalled when its newest checkpoint is
    # older than the cutoff (or it never checkpointed
    # and started before the cutoff).
    running = frappe.get_all(
        "SyncJob",
        filters={"status": "Running"},
        fields=["name", "started_at"],
    )

    recovered = 0
    for job in running:
        newest = frappe.db.get_value(
            "SyncJobCheckpoint",
            {"parent": job["name"]},
            "checkpointed_at",
            order_by="checkpointed_at desc",
        )
        # Never checkpointed: fall back to started_at
        last_progress = newest or job.get("started_at")
        if last_progress is None or last_progress <= cutoff:
            frappe.db.set_value(
                "SyncJob", job["name"], "status", "Interrupted"
            )
            frappe.db.commit()
            logger.info(
                "Recovered stalled job %s (last progress %s)",
                job["name"], last_progress,
            )
            recovered += 1

    if recovered:
        logger.info("Recovered %d stalled jobs", recovered)
    return recovered
