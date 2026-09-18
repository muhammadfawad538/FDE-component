"""
Patch: add composite unique index on SyncJobDedup(job_type, idempotency_key).

Frappe's DocType JSON only supports single-field unique indexes.  The dedup
guarantee requires that the pair (job_type, idempotency_key) be unique — a
composite index added via raw SQL after the table is created.

This patch runs once after `bench migrate` creates the table, or on app
update if the index is missing.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    index_name = "unique_job_type_idempotency_key"
    table = "tabSyncJobDedup"

    # Idempotent — skip if index already exists
    existing = frappe.db.sql(
        f"SHOW INDEX FROM `{table}` WHERE Key_name = %s",
        (index_name,),
    )
    if existing:
        frappe.db.commit()
        return

    frappe.db.sql(
        f"""
        ALTER TABLE `{table}`
        ADD UNIQUE INDEX `{index_name}` (`job_type`, `idempotency_key`)
        """
    )
    frappe.db.commit()
