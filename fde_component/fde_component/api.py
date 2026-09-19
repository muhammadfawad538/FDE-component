"""
fde_component.api
=================
Public API methods callable from the frontend.
"""

import frappe
from fde_component.jobs.exceptions import JobDeadLettered, JobInterrupted


@frappe.whitelist()
def redrive_from_dlq(job_name: str) -> dict:
    """
    Re-drive a Dead Lettered job.
    Resets job state and enqueues it for re-processing.
    """
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
    for dlq in frappe.get_all("SyncJobDLQ", filters={"parent": job_name}):
        frappe.delete_doc("SyncJobDLQ", dlq.name)

    # Enqueue the job
    frappe.enqueue(
        "fde_component.jobs.runner.run_job",
        queue="default",
        sync_job_name=job_name,
        job_type=doc.job_type
    )

    return {"success": True, "message": "Job re-drive initiated"}


@frappe.whitelist()
def resume_job(job_name: str) -> dict:
    """
    Resume an Interrupted job from its last checkpoint.
    """
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
        job_type=doc.job_type
    )

    return {"success": True, "message": "Job resumed"}
