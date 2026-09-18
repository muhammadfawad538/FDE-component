"""
fde_component.doctypes.sync_job_dlq.sync_job_dlq
===================================================
SyncJobDLQ DocType class.
"""

import frappe


def re_drive(doc: dict, method: str | None = None) -> None:
    """
    Server action: create a new SyncJob for this DLQ item and set
    ``re_drive_status = "Re-driven"``.
    """
    new_job = frappe.new_doc("SyncJob")
    new_job.job_type = frappe.db.get_value("SyncJob", doc.sync_job, "job_type")
    new_job.status = "Queued"
    new_job.source_config = frappe.db.get_value("SyncJob", doc.sync_job, "source_config") or "{}"
    new_job.insert(ignore_permissions=True)
    frappe.db.commit()

    doc.status = "Re-driven"
    doc.re_drive_job = new_job.name
    doc.re_driven_at = frappe.utils.now_datetime()

    # Append to audit log
    entry = {"timestamp": frappe.utils.now_datetime(), "action": "re_driven", "job": new_job.name}
    audit = frappe.parse_json(doc.audit or "[]")
    audit.append(entry)
    doc.audit = frappe.as_json(audit)

    doc.save(ignore_permissions=True)
    frappe.db.commit()
