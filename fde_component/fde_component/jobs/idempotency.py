"""
fde_component.jobs.idempotency
================================
Idempotency key builder and dedup checker for SP-05.

Guarantees
----------
* A record is processed at most once per job_type, regardless of how many
  times the worker retries or how many parallel workers run.
* The unique DB index on ``SyncJobDedup(job_type, idempotency_key)`` means
  concurrent workers racing on the same key get ``frappe.db.UniqueValidationError``
  on insert — caught and treated as "already seen."
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
                   (e.g. a message-id, file path, database primary key)
    ``content``   — optional raw bytes or string; included in the hash so
                   that identical source_ids with different content are
                   treated as *different* records.

    Construction
    ------------
    Either pass the three fields separately, or supply a pre-computed hex
    digest via ``from_hex`` (useful when persisting/restoring keys).
    """

    __slots__ = ("_digest",)

    def __init__(self, job_type: str, source_id: str, content: str | bytes = "") -> None:
        raw = f"{job_type}\x00{source_id}\x00{content}"
        self._digest: str = hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def from_hex(cls, digest: str) -> "IdempotencyKey":
        """Reconstruct from a stored hex digest (no source fields needed)."""
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


def _dedup_doc_exists(job_type: str, idempotency_key_hex: str) -> bool:
    """Return True if a dedup record already exists for this key."""
    return frappe.db.exists(_DEDUP_DOCTYPE, {
        "job_type": job_type,
        "idempotency_key": idempotency_key_hex,
    })


def check_dedup(job_type: str, key: IdempotencyKey) -> None:
    """
    Check whether *key* has already been processed for *job_type*.

    Raises
    ------
    DedupDuplicate
        If the key is already in ``SyncJobDedup`` — the caller should skip
        this record and count it as a duplicate.
    """
    if _dedup_doc_exists(job_type, key.hex):
        raise DedupDuplicate(f"Duplicate: {key.hex}")


def mark_dedup(
    job_type: str,
    key: IdempotencyKey,
    source_id: str,
    sync_job_id: str,
) -> None:
    """
    Record that *key* has been processed.

    If two workers race on the same key, one insert succeeds and the other
    raises ``frappe.db.UniqueValidationError`` (the DB enforces the unique
    index on ``(job_type, idempotency_key)``).  We catch that and treat it
    as "already seen" — processing continues.

    Parameters
    ----------
    job_type      : registered job type name
    key           : IdempotencyKey instance for this record
    source_id     : caller-supplied identifier (stored for audit / debugging)
    sync_job_id   : the SyncJob doc name — ties the dedup entry to a run
    """
    try:
        doc = frappe.new_doc(_DEDUP_DOCTYPE)
        doc.job_type = job_type
        doc.idempotency_key = key.hex
        doc.source_id = source_id
        doc.sync_job_id = sync_job_id
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except frappe.db.UniqueValidationError:
        # Concurrent worker won the race — dedup record already exists.
        frappe.db.rollback()
    except Exception:
        # Any other failure is non-fatal for dedup — skip and continue.
        frappe.db.rollback()


def is_deduped(job_type: str, key: IdempotencyKey) -> bool:
    """Convenience: return True if already seen, False if new."""
    return _dedup_doc_exists(job_type, key.hex)
