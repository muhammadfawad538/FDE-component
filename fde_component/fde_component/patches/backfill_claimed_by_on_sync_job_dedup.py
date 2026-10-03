"""
Patch: backfill claimed_by on SyncJobDedup.

The two-phase dedup feature added a ``claimed_by`` field that tracks which
SyncJob claimed a record. Existing rows were created before this field existed,
so ``claimed_by`` is NULL for them.

This patch copies ``sync_job_id`` into ``claimed_by`` for all existing rows,
which is correct because ``claim_dedup`` always sets both to the same value.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    table = "tabSyncJobDedup"

    # 1. Add claimed_by column if missing
    columns = frappe.db.sql(f"SHOW COLUMNS FROM `{table}` WHERE Field = 'claimed_by'")
    if not columns:
        frappe.db.sql(
            f"""
            ALTER TABLE `{table}`
            ADD COLUMN `claimed_by` VARCHAR(140) DEFAULT NULL
            """
        )
        frappe.db.commit()

    # 2. Backfill claimed_by from sync_job_id for existing rows
    updated = frappe.db.sql(
        f"""
        UPDATE `{table}`
        SET `claimed_by` = `sync_job_id`
        WHERE `claimed_by` IS NULL OR `claimed_by` = ''
        """
    )
    frappe.db.commit()
