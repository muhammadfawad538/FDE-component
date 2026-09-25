"""
fde_component.doctypes.sync_job.sync_job
=========================================
SyncJob DocType class — primarily a hook target; most logic lives in
``fde_component.jobs.base.CheckpointedJob``.
"""

import frappe


def set_job_defaults(doc: dict) -> None:
    """
    Hook called before a SyncJob is inserted.
    Sets defaults that Frappe's DocType JSON can't express cleanly.
    """
    if not doc.source_config:
        doc.source_config = "{}"
    doc.total_records = doc.total_records or 0
    doc.processed = doc.processed or 0
    doc.failed_count = doc.failed_count or 0
    doc.retry_count = doc.retry_count or 0
