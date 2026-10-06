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

    Clears ONLY the failed record's dedup row so the new job can reprocess
    it. Other completed records' dedup state is preserved.

    The new job inherits the parent job's checksum lineage via source_config.
    """
    import json

    # Reject empty/None idempotency_key — a DLQ entry without a key cannot
    # be scoped to a single record for redrive.
    if not doc.idempotency_key:
        frappe.throw("Cannot re-drive a DLQ entry with an empty idempotency key")

    parent = frappe.get_doc("SyncJob", doc.sync_job)

    # Atomically claim this DLQ entry: only transition to Re-driven if it
    # is not already Re-driven. This prevents two concurrent calls from
    # creating two child jobs for the same failed record.
    frappe.db.sql(
        """
        UPDATE `tabSyncJobDLQ`
        SET `status` = 'Re-driven'
        WHERE `name` = %s AND `status` != 'Re-driven'
        """,
        (doc.name,),
    )
    if frappe.db._cursor.rowcount == 0:
        return  # already claimed by another call

    new_job = frappe.new_doc("SyncJob")
    new_job.job_type = parent.job_type
    new_job.status = "Queued"
    new_job.parent_job = parent.name

    # Scope the new job to ONLY the failed record
    try:
        source_config = json.loads(parent.source_config or "{}")
    except Exception:
        source_config = {}

    # Inherit parent's checksum lineage: look up parent's last checkpoint
    parent_last_checksum = frappe.db.get_value(
        "SyncJobCheckpoint",
        {"parent": parent.name},
        "checksum",
        order_by="idx desc",
    )
    if parent_last_checksum:
        source_config["parent_checksum"] = parent_last_checksum

    source_config["redrive_key"] = doc.idempotency_key
    source_config["redrive_parent"] = doc.sync_job
    new_job.source_config = json.dumps(source_config)

    new_job.insert(ignore_permissions=True)
    frappe.db.commit()

    # Clear ONLY the failed record's dedup row
    frappe.db.delete(
        "SyncJobDedup",
        {
            "job_type": new_job.job_type,
            "idempotency_key": doc.idempotency_key,
            "status": "completed",
        },
    )
    frappe.db.commit()

    # Append to audit log
    entry = {"timestamp": frappe.utils.now_datetime(), "action": "re_driven", "job": new_job.name}
    audit = frappe.parse_json(doc.audit or "[]")
    audit.append(entry)
    doc.audit = frappe.as_json(audit)

    doc.re_drive_job = new_job.name
    doc.re_driven_at = frappe.utils.now_datetime()
    doc.save(ignore_permissions=True)
    frappe.db.commit()
