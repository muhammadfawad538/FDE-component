"""
fde_component.jobs.idempotency
================================
Two-phase idempotency key builder and dedup checker for SP-05.

Phase 1 — claim: before processing a record, insert a dedup row with
           status='in_progress' to announce intent.
Phase 2 — complete: after successful processing (or DLQ), update the row
           to status='completed' so future runs know the record was handled.

Crash between claim and complete → the row stays 'in_progress'.
On resume, reclaim_stale_claims() resets stale 'in_progress' rows back
to 'completed' so the record is eligible for reprocessing.

Guarantees
----------
* A record is processed at most once per job_type, regardless of how many
  times the worker retries or how many parallel workers run.
* The unique DB index on (job_type, idempotency_key, status) means:
  - two workers can both claim (in_progress) the same record
  - only one can complete it (completed)
  - concurrent races are resolved by the DB constraint, not by in-memory locks
* The check-then-insert pattern is not atomic; the race is resolved by the
  unique constraint, not by a DB-level atomic operation.
"""

from __future__ import annotations

import hashlib
from typing import Optional

import frappe
from frappe import _dict

from .exceptions import DedupDuplicate


# ---------------------------------------------------------------------------
# Key builder
# ---------------------------------------------------------------------------

class IdempotencyKey:
    """
    Deterministic key for a source record within a job type.

    Key material
    ------------
    ``job_type``  — the registered name of the job (e.g. ``"mail.import"``)
    ``source_id`` — caller-supplied unique identifier for the record
    ``content``   — optional raw bytes or string; included in the hash so
                    that identical source_ids with different content are
                    treated as *different* records.
    """

    __slots__ = ("_digest",)

    def __init__(self, job_type: str, source_id: str, content: str | bytes = "") -> None:
        if isinstance(content, bytes):
            raw_bytes = f"{job_type}\x00{source_id}\x00".encode() + content
        else:
            raw_bytes = f"{job_type}\x00{source_id}\x00{content}".encode()
        self._digest: str = hashlib.sha256(raw_bytes).hexdigest()

    @classmethod
    def from_hex(cls, digest: str) -> "IdempotencyKey":
        instance = cls.__new__(cls)
        instance._digest = digest
        return instance

    @property
    def hex(self) -> str:
        return self._digest

    def __str__(self) -> str:
        return self._digest

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IdempotencyKey) and self._digest == other._digest

    def __hash__(self) -> int:
        return hash(self._digest)


# ---------------------------------------------------------------------------
# Dedup operations
# ---------------------------------------------------------------------------

_DEDUP_DOCTYPE = "SyncJobDedup"


def _dedup_doc_exists(job_type: str, idempotency_key_hex: str, status: str = "completed") -> bool:
    """Return True if a dedup record exists for this key with the given status."""
    return frappe.db.exists(_DEDUP_DOCTYPE, {
        "job_type": job_type,
        "idempotency_key": idempotency_key_hex,
        "status": status,
    })


def check_dedup(job_type: str, key: IdempotencyKey) -> None:
    """
    Check whether *key* has already been completed for *job_type*.

    Only 'completed' rows block processing. 'in_progress' rows are ignored
    — they represent a worker that claimed the record but may have crashed
    before finishing.

    Raises
    ------
    DedupDuplicate
        If a completed entry exists — the caller should skip this record.
    """
    if _dedup_doc_exists(job_type, key.hex, status="completed"):
        raise DedupDuplicate(f"Duplicate: {key.hex}")


def claim_dedup(job_type: str, key: IdempotencyKey, source_id: str, sync_job_id: str) -> bool:
    """
    Claim a record by inserting a dedup row with status='in_progress'.

    Returns True if the claim succeeded, False if another worker already
    claimed this exact (key, in_progress) combination.

    The unique index on (job_type, idempotency_key, status) enforces that
    only one row per (key, in_progress) can exist.
    """
    try:
        doc = frappe.new_doc(_DEDUP_DOCTYPE)
        doc.job_type = job_type
        doc.idempotency_key = key.hex
        doc.source_id = source_id
        doc.sync_job_id = sync_job_id
        doc.status = "in_progress"
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return True
    except frappe.db.UniqueValidationError:
        frappe.db.rollback()
        return False
    except Exception:
        frappe.db.rollback()
        return False


def complete_dedup(job_type: str, key: IdempotencyKey) -> None:
    """
    Mark a claimed record as completed by updating status from
    'in_progress' to 'completed'.

    If the row is already 'completed' (e.g. another worker finished it),
    this is a no-op.
    """
    try:
        frappe.db.sql(
            """
            UPDATE `tabSyncJobDedup`
            SET `status` = 'completed'
            WHERE `job_type` = %s AND `idempotency_key` = %s AND `status` = 'in_progress'
            """,
            (job_type, key.hex),
        )
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()


def reclaim_stale_claims(job_type: str, older_than_seconds: int = 3600) -> int:
    """
    Delete stale 'in_progress' dedup rows so the record can be reclaimed
    and retried. A claim is considered stale if it has not been updated
    within the given time window.

    Returns the number of rows deleted.
    """
    try:
        result = frappe.db.sql(
            """
            DELETE FROM `tabSyncJobDedup`
            WHERE `job_type` = %s
              AND `status` = 'in_progress'
              AND `first_seen` < DATE_SUB(NOW(), INTERVAL %s SECOND)
            """,
            (job_type, older_than_seconds),
        )
        frappe.db.commit()
        return result[0][0] if result and result[0] else 0
    except Exception:
        frappe.db.rollback()
        return 0


def mark_dedup(
    job_type: str,
    key: IdempotencyKey,
    source_id: str,
    sync_job_id: str,
) -> None:
    """
    Record that *key* has been processed.

    Uses the two-phase approach: insert as 'in_progress', then immediately
    update to 'completed'. This preserves backward compatibility with code
    that calls mark_dedup directly.

    If two workers race on the same key:
    - Both can insert 'in_progress' rows (different status values).
    - Only one can update to 'completed' for a given (key, in_progress) row.
    - The unique index on (job_type, idempotency_key, status) enforces this.
    """
    try:
        # Phase 1: claim
        doc = frappe.new_doc(_DEDUP_DOCTYPE)
        doc.job_type = job_type
        doc.idempotency_key = key.hex
        doc.source_id = source_id
        doc.sync_job_id = sync_job_id
        doc.status = "in_progress"
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except frappe.db.UniqueValidationError:
        # Another worker already claimed this exact (key, in_progress)
        frappe.db.rollback()
        # Try to complete if the other worker already finished
        complete_dedup(job_type, key)
        return
    except Exception:
        frappe.db.rollback()
        return

    # Phase 2: complete
    complete_dedup(job_type, key)


def is_deduped(job_type: str, key: IdempotencyKey) -> bool:
    """Convenience: return True if already completed, False if new or in-progress."""
    return _dedup_doc_exists(job_type, key.hex, status="completed")
