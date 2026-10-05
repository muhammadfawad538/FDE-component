"""
fde_component.jobs.runner
==========================
RQ job runner — entry point called by Frappe's queue when a SyncJob is
enqueued.  Resolves the CheckpointedJob subclass from ``job_type`` and
executes it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import frappe

from .base import CheckpointedJob
from .exceptions import JobFailed

logger = logging.getLogger(__name__)


# Registry of known job types → CheckpointedJob subclasses.
# Add new job types here; or use a decorator-based registry on the subclass.
JOB_REGISTRY: dict[str, type[CheckpointedJob]] = {}


def register_job(job_type: str) -> Callable[[type[CheckpointedJob]], type[CheckpointedJob]]:
    """
    Decorator to register a CheckpointedJob subclass in ``JOB_REGISTRY``.

    Usage
    -----
    .. code-block:: python

        @register_job("mail.import")
        class MailImportJob(CheckpointedJob):
            job_type = "mail.import"
            ...
    """
    def decorator(cls: type[CheckpointedJob]) -> type[CheckpointedJob]:
        JOB_REGISTRY[job_type] = cls
        return cls
    return decorator


def run_job(sync_job_name: str, job_type: str, **kwargs: Any) -> None:
    """
    RQ entry point.  Resolves the job class, instantiates it, and runs it.

    Called by Frappe's queue worker with keyword args from the enqueue call.
    """
    # Frappe enqueue calls this as a method path; frappe.enqueue resolves it.
    # We need frappe.init() etc. — the RQ worker has already done that.

    # Atomically claim the job: only transition from Queued/Interrupted to Running.
    # This prevents two workers from running the same job concurrently.
    frappe.db.sql(
        """
        UPDATE `tabSyncJob`
        SET `status` = 'Running',
            `started_at` = NOW()
        WHERE `name` = %s
          AND `status` IN ('Queued', 'Interrupted')
        """,
        (sync_job_name,),
    )
    # frappe.db.affected_rows() is not exposed on MariaDBDatabase;
    # check the raw cursor rowcount to see if the claim succeeded.
    if frappe.db._cursor.rowcount == 0:
        sync_job = frappe.get_doc("SyncJob", sync_job_name)
        logger.info(
            "Job %s already claimed (status=%s) — skipping",
            sync_job_name,
            sync_job.status,
        )
        return

    job_cls = JOB_REGISTRY.get(job_type)
    if job_cls is None:
        frappe.db.set_value("SyncJob", sync_job_name, {"status": "Failed"})
        frappe.db.commit()
        raise JobFailed(f"No CheckpointedJob registered for job_type={job_type!r}")

    # Extract parent_checksum from source_config for redrive lineage
    parent_checksum = None
    try:
        source_config = frappe.db.get_value("SyncJob", sync_job_name, "source_config") or "{}"
        config = frappe.parse_json(source_config)
        parent_checksum = config.get("parent_checksum")
    except Exception:
        pass

    job = job_cls(job_name=sync_job_name, parent_checksum=parent_checksum, **kwargs)
    try:
        job.run()
    except Exception:
        frappe.db.rollback()
        logger.exception("Job %s (%s) crashed", sync_job_name, job_type)
        frappe.db.set_value("SyncJob", sync_job_name, {"status": "Failed"})
        frappe.db.commit()
        raise
