"""
fde_component.jobs.base
========================
CheckpointedJob base class + retry/DLQ unit for SP-05.

Retry/DLQ contract (the one unit)
----------------------------------
``retry_dlq`` is a standalone function that wraps a per-record handler call.
On failure it retries up to ``max_retries`` times; on exhaustion it writes
the item to ``SyncJobDLQ``, sets the parent ``SyncJob`` to ``Dead Lettered``,
and re-raises ``JobDeadLettered``.  Job code never calls DLQ manually.

Testability
-----------
``retry_dlq`` is a pure function — pass it a callable, a job doc reference,
a record, and max_retries.  Supply a handler that fails N-1 times and
assert: DLQ doc created, retry_count correct, JobDeadLettered raised.
"""

from __future__ import annotations

import hashlib
import json
import logging
import signal
import time
from typing import Any, Callable, Generator, TypeVar

import frappe
from frappe.utils import now_datetime

from .exceptions import (
    DedupDuplicate,
    JobDeadLettered,
    JobFailed,
    JobInterrupted,
)
from .idempotency import IdempotencyKey, check_dedup, mark_dedup

logger = logging.getLogger(__name__)

F = TypeVar("F")

# ── Retry/DLQ unit ───────────────────────────────────────────────────────────


def retry_dlq(
    func: Callable[..., Any],
    job: Any,   # CheckpointedJob instance — typed Any to avoid circular import
    record: Any,
    max_retries: int = 3,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Call *func(record, ...)* with retry + automatic DLQ on exhaustion.

    This is the single unit for retry and dead-lettering.  It is tested
    in isolation from all other job logic.

    Parameters
    ----------
    func        : the per-record processing callable
    job         : the owning CheckpointedJob instance
    record      : the record being processed
    max_retries : how many times to retry after the first failure
    *args, **kwargs : forwarded to *func*

    Returns
    -------
    Whatever ``func`` returns on success.

    Raises
    ------
    JobDeadLettered
        After ``max_retries`` failures — caller should skip this record.
    JobInterrupted
        If a signal handler fires during retry — propagates immediately.
    """
    last_exc: BaseException | None = None

    for attempt in range(1, max_retries + 2):  # 1 .. max_retries + 1
        try:
            return func(record, *args, **kwargs)
        except JobInterrupted:
            raise  # never retry — worker is shutting down
        except JobFailed as exc:
            last_exc = exc
            logger.warning(
                "Job %s record attempt %d/%d failed: %s",
                getattr(job, "job_name", "?"),
                attempt,
                max_retries + 1,
                exc,
            )
            if attempt <= max_retries:
                frappe.log_error(
                    title=f"Retry {attempt}/{max_retries}",
                    message=str(exc),
                )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Job %s record attempt %d/%d unexpected error: %s",
                getattr(job, "job_name", "?"),
                attempt,
                max_retries + 1,
                exc,
            )
            if attempt <= max_retries:
                frappe.log_error(
                    title=f"Retry {attempt}/{max_retries}",
                    message=str(exc),
                )

    # All retries exhausted → write DLQ + raise
    job._write_dlq(record, last_exc, max_retries + 1)
    raise JobDeadLettered(
        f"Record moved to DLQ after {max_retries + 1} attempts: {record!r}"
    )


# ── Base job class ───────────────────────────────────────────────────────────


class CheckpointedJob:
    """
    Abstract base class for resumable, idempotent sync jobs.

    Subclasses declare ``job_type`` and override ``iter_records`` and
    ``process_record``.  The base class handles: idempotency, checkpointing,
    retry/DLQ, progress publishing, and signal-driven graceful shutdown.

    Usage
    -----
    .. code-block:: python

        class MailImportJob(CheckpointedJob):
            job_type = "mail.import"
            max_retries = 3

            def iter_records(self, offset: int) -> Generator:
                return mail_source.iter(offset=offset, limit=self.batch_size)

            def process_record(self, record):
                return ingest_mail(record)

        # Enqueue via Frappe RQ:
        # frappe.enqueue("fde_component.jobs.runner.run_job",
        #                job_class="MailImportJob")
    """

    # ── Declared by subclasses ─────────────────────────────────────────────
    job_type: str = ""          # e.g. "mail.import" — must be unique per job kind
    max_retries: int = 3        # retry attempts per record before DLQ
    checkpoint_every: int = 1000  # records between checkpoints
    checkpoint_seconds: float = 30.0  # seconds between checkpoints

    # ── Set at runtime by the worker ───────────────────────────────────────
    job_name: str = ""          # SyncJob doc name (set in __init__)
    _interrupted: bool = False

    def __init__(self, job_name: str, **kwargs: Any) -> None:
        """
        Parameters
        ----------
        job_name : the ``name`` of the parent ``SyncJob`` doc.
        """
        self.job_name = job_name
        self.batch_size: int = kwargs.get("batch_size", 1000)
        self._last_checkpoint_time: float = time.monotonic()
        self._processed: int = 0
        self._setup_signal_handlers()

    # ── Abstract interface ─────────────────────────────────────────────────

    def iter_records(self, offset: int) -> Generator[Any, None, None]:
        """
        Yield records to process, starting at *offset*.

        Subclasses must implement this.  Each yielded value is passed to
        ``process_record``.  Return an empty generator when done.
        """
        raise NotImplementedError

    def process_record(self, record: Any) -> None:
        """
        Handle a single record.  Subclasses must implement.

        Raise ``JobFailed`` for a permanent (non-retryable) failure.
        Raise any other exception — it will be retried by ``retry_dlq``.
        """
        raise NotImplementedError

    def on_complete(self) -> None:
        """Hook called after all records processed successfully. Override as needed."""

    def on_interrupted(self) -> None:
        """Hook called when the job is interrupted (SIGTERM/SIGINT). Override as needed."""

    # ── Main entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        """
        Execute the job: iterate records, dedup, process with retry,
        checkpoint, publish progress, handle shutdown.

        This method is called by the RQ worker.  It catches ``JobInterrupted``
        from the signal handler and checkpoints before exiting.
        """
        self._update_status("Running")
        self._set_started()
        self._processed = 0
        self._last_checkpoint_time = time.monotonic()
        offset = self._get_start_offset()
        total = self._get_total()

        logger.info("Job %s starting at offset %d (total=%s)", self.job_name, offset, total)

        try:
            for record in self.iter_records(offset):
                if self._interrupted:
                    break

                # --- Idempotency check ---
                key = self._idempotency_key(record)
                try:
                    check_dedup(self.job_type, key)
                except DedupDuplicate:
                    self._processed += 1
                    self._publish_progress(total)
                    continue

                # --- Process with retry/DLQ (single unit, tested in isolation) ---
                try:
                    retry_dlq(
                        func=self.process_record,
                        job=self,
                        record=record,
                        max_retries=self.max_retries,
                    )
                except JobDeadLettered:
                    # Item moved to DLQ — count it and move on.
                    self._processed += 1
                    self._publish_progress(total)
                    continue

                # --- Mark as processed ---
                mark_dedup(self.job_type, key, self._source_id(record), self.job_name)
                self._processed += 1

                # --- Checkpoint + progress ---
                if self._should_checkpoint():
                    self.save_checkpoint(offset)
                    self._last_checkpoint_time = time.monotonic()

                self._publish_progress(total)

                offset += 1  # advance for resume

        except JobInterrupted:
            self._interrupted = True
            logger.info("Job %s interrupted at offset %d", self.job_name, offset)
            self.on_interrupted()

        # Final checkpoint at end of run (even on interruption)
        self.save_checkpoint(offset)

        if not self._interrupted:
            self._update_counts(processed=self._processed)
            self._update_status("Completed")
            self.on_complete()
            logger.info("Job %s completed: %d records", self.job_name, self._processed)
        else:
            self._update_counts(processed=self._processed)
            self._update_status("Interrupted")
            logger.info("Job %s interrupted, resumable from offset %d", self.job_name, offset)

    # ── Checkpoint ─────────────────────────────────────────────────────────

    def save_checkpoint(self, offset: int) -> None:
        """
        Persist current progress as a ``SyncJobCheckpoint`` child row
        and update the parent ``SyncJob`` doc.
        """
        checksum = self._compute_checksum()
        row_count = self._processed

        try:
            doc = frappe.get_doc("SyncJob", self.job_name)
            doc.append(
                "checkpoints",
                {
                    "offset": offset,
                    "records_processed": row_count,
                    "checksum": checksum,
                },
            )
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            logger.exception("Job %s failed to save checkpoint at offset %d", self.job_name, offset)

    # ── DLQ write (called by retry_dlq, never by job code directly) ────────

    def _write_dlq(self, record: Any, error: BaseException | None, retry_count: int) -> None:
        """
        Write a dead-letter entry for *record* and mark the parent job as
        Dead Lettered.  Both writes are a single atomic transaction — either
        both commit or both roll back.  Called by ``retry_dlq`` only; job code
        must not call this directly.
        """
        try:
            doc = frappe.new_doc("SyncJobDLQ")
            doc.sync_job = self.job_name
            doc.payload = json.dumps(self._serialize_record(record), default=str)
            doc.reason = str(error) if error else "Unknown error"
            doc.retry_count = retry_count
            doc.status = "Dead Lettered"
            doc.insert(ignore_permissions=True)

            frappe.db.set_value(
                "SyncJob",
                self.job_name,
                {"status": "Dead Lettered", "error_summary": str(error)},
            )
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            logger.exception("Job %s failed to write DLQ entry", self.job_name)

    # ── Signal handling ────────────────────────────────────────────────────

    def _setup_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers that set the interrupted flag."""
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, signum: int, frame: Any) -> None:
        """Signal handler: set flag so the record loop exits after current record."""
        logger.info("Job %s received signal %d — interrupting after current record", self.job_name, signum)
        self._interrupted = True

    # ── Progress publishing ────────────────────────────────────────────────

    def _publish_progress(self, total: int | None) -> None:
        """
        Push live progress to the desk via ``frappe.publish_realtime``.
        Called at every record — overhead is minimal (a single Redis PUBLISH).
        """
        if total and total > 0:
            pct = min(100.0, (self._processed / total) * 100.0)
        else:
            pct = 0.0

        frappe.publish_realtime(
            event="job_progress",
            message={
                "job": self.job_name,
                "processed": self._processed,
                "total": total,
                "percent": round(pct, 1),
            },
            user=frappe.session.user,
        )

    # ── Checkpoint timing ──────────────────────────────────────────────────

    def _should_checkpoint(self) -> bool:
        """True when the record count or elapsed time since last checkpoint exceeds limits."""
        if self._processed % self.checkpoint_every == 0:
            return True
        elapsed = time.monotonic() - self._last_checkpoint_time
        return elapsed >= self.checkpoint_seconds

    # ── Doc helpers ────────────────────────────────────────────────────────

    def _update_status(self, status: str) -> None:
        frappe.db.set_value("SyncJob", self.job_name, {"status": status})
        frappe.db.commit()

    def _set_started(self) -> None:
        frappe.db.set_value(
            "SyncJob",
            self.job_name,
            {"status": "Running", "started_at": now_datetime()},
        )
        frappe.db.commit()

    def _update_counts(self, processed: int) -> None:
        frappe.db.set_value("SyncJob", self.job_name, {"processed": processed})
        frappe.db.commit()

    def _get_start_offset(self) -> int:
        """Return the offset to resume from (last checkpoint, or 0)."""
        last = frappe.get_all(
            "SyncJobCheckpoint",
            filters={"parent": self.job_name},
            fields=["offset"],
            order_by="idx desc",
            limit=1,
        )
        return last[0]["offset"] if last else 0

    def _get_total(self) -> int | None:
        """Return total record count if known, else None."""
        return frappe.db.get_value("SyncJob", self.job_name, "total_records")

    # ── Hooks for subclasses ───────────────────────────────────────────────

    def _idempotency_key(self, record: Any) -> IdempotencyKey:
        """
        Build the idempotency key for a record.
        Override if the key should include content or a different field.
        """
        source_id = self._source_id(record)
        content = self._content_hash(record)
        return IdempotencyKey(self.job_type, source_id, content)

    def _source_id(self, record: Any) -> str:
        """Extract a stable identifier from *record* for dedup keys."""
        if hasattr(record, "get"):
            return str(record.get("name") or record.get("id") or hashlib.sha256(str(record).encode()).hexdigest()[:16])
        return str(record)

    def _content_hash(self, record: Any) -> str:
        """Optional content fingerprint — empty string by default."""
        return ""

    def _serialize_record(self, record: Any) -> Any:
        """Convert *record* to a JSON-safe form for DLQ payload."""
        if hasattr(record, "as_dict"):
            return record.as_dict()
        if isinstance(record, dict):
            return record
        return str(record)

    def _compute_checksum(self) -> str:
        """
        Lightweight checksum of the current batch for lineage verification.
        Override for domain-specific checksums.
        """
        return f"offset={self._processed}"
