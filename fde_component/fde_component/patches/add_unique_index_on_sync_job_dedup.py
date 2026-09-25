"""
Patch: migrate SyncJobDedup to two-phase dedup with status column.

Adds a `status` field ('in_progress' | 'completed') and changes the unique
index from (job_type, idempotency_key) to (job_type, idempotency_key, status).

This allows a record to be claimed (in_progress) and later completed without
violating the unique constraint. A stale in_progress row from a crashed worker
can be reclaimed by a retry.

Existing rows are migrated to status='completed'.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    table = "tabSyncJobDedup"
    old_index = "unique_job_type_idempotency_key"
    new_index = "unique_job_type_idempotency_key_status"

    # 1. Drop old unique index if it exists
    existing = frappe.db.sql(
        f"SHOW INDEX FROM `{table}` WHERE Key_name = %s",
        (old_index,),
    )
    if existing:
        frappe.db.sql(f"ALTER TABLE `{table}` DROP INDEX `{old_index}`")
        frappe.db.commit()

    # 2. Add status column if missing
    columns = frappe.db.sql(f"SHOW COLUMNS FROM `{table}` WHERE Field = 'status'")
    if not columns:
        frappe.db.sql(
            f"""
            ALTER TABLE `{table}`
            ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'completed'
            """
        )
        frappe.db.commit()

    # 3. Migrate existing rows to status='completed'
    frappe.db.sql(
        f"UPDATE `{table}` SET `status` = 'completed' WHERE `status` IS NULL OR `status` = ''"
    )
    frappe.db.commit()

    # 4. Add new unique index on (job_type, idempotency_key, status)
    existing = frappe.db.sql(
        f"SHOW INDEX FROM `{table}` WHERE Key_name = %s",
        (new_index,),
    )
    if existing:
        frappe.db.commit()
        return

    frappe.db.sql(
        f"""
        ALTER TABLE `{table}`
        ADD UNIQUE INDEX `{new_index}` (`job_type`, `idempotency_key`, `status`)
        """
    )
    frappe.db.commit()
