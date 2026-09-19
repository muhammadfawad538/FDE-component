"""
fde_component.doctypes.sync_schedule.sync_schedule
===================================================
SyncSchedule DocType class.
"""

import frappe


def validate_cron(doc, method):
    if not doc.cron:
        return
    try:
        from croniter import croniter
        croniter(doc.cron)
    except Exception:
        frappe.throw("Invalid cron expression")
