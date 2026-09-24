"""
fde_component.api
=================
Public API methods callable from the frontend.
"""

import frappe
from fde_component.jobs.exceptions import JobDeadLettered


@frappe.whitelist()
def redrive_from_dlq(job_name: str) -> dict:
    """
    Re-drive a Dead Lettered job.
    Resets job state and enqueues it for re-processing.
    """
    try:
        doc = frappe.get_doc("SyncJob", job_name)

        if doc.status != "Dead Lettered":
            frappe.throw(f"Job {job_name} is not Dead Lettered")

        # Reset job state
        doc.status = "Queued"
        doc.error_summary = ""
        doc.completed_at = ""
        doc.retry_count = 0
        doc.failed_count = 0
        doc.processed = 0
        doc.save()

        # Delete old DLQ entries for this job
        dlq_names = frappe.get_all("SyncJobDLQ", filters={"sync_job": job_name}, pluck="name")
        for dlq_name in dlq_names:
            frappe.delete_doc("SyncJobDLQ", dlq_name, ignore_permissions=True)

        # Clear dedup state so the redriven job can reprocess its records
        frappe.db.delete("SyncJobDedup", {"sync_job_id": job_name})

        # Enqueue the job
        frappe.enqueue(
            "fde_component.jobs.runner.run_job",
            queue="default",
            sync_job_name=job_name,
            job_type=doc.job_type,
        )

        frappe.db.commit()
        return {"success": True, "message": "Job re-drive initiated"}

    except Exception:
        frappe.db.rollback()
        raise


@frappe.whitelist()
def resume_job(job_name: str) -> dict:
    """
    Resume an Interrupted job from its last checkpoint.
    """
    try:
        doc = frappe.get_doc("SyncJob", job_name)

        if doc.status != "Interrupted":
            frappe.throw(f"Job {job_name} is not Interrupted")

        # Reset to Running
        doc.status = "Running"
        doc.started_at = frappe.utils.now()
        doc.save()

        # Enqueue the job
        frappe.enqueue(
            "fde_component.jobs.runner.run_job",
            queue="default",
            sync_job_name=job_name,
            job_type=doc.job_type,
        )

        frappe.db.commit()
        return {"success": True, "message": "Job resumed"}

    except Exception:
        frappe.db.rollback()
        raise
