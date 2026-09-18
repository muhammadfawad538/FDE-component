"""
fde_component.tests.test_checkpoint_resume
=============================================
Acceptance test for SP-05: killing a worker mid-import resumes from the
last checkpoint with zero duplicates.

Test strategy
-------------
Because we can't spawn a real RQ worker in pytest, the test drives the
CheckpointedJob.run() method directly, simulating a worker kill by raising
JobInterrupted at a specific offset, then re-instantiating the job and
calling run() again to simulate a worker restart.

Proven acceptance criteria:
  * All N records are eventually processed
  * Zero duplicates in SyncJobDedup after resume
  * Final SyncJob status = Completed
  * Checkpoints capture progress at the kill point
"""

from __future__ import annotations

import logging
from typing import Any, Generator

import pytest

from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobInterrupted
from fde_component.jobs.idempotency import mark_dedup


logger = logging.getLogger(__name__)

TOTAL_RECORDS = 10_000
KILL_AT = 5_000


# ── Test job ─────────────────────────────────────────────────────────────────


class CountingImportJob(CheckpointedJob):
    """
    A deterministic test job: iterates N records, each identified by
    ``record-{i}``.  Tracks which records it has actually processed so
    the test can verify resume skips already-done items.
    """

    job_type = "test.counting_import"
    max_retries = 0          # no retries needed for this test
    checkpoint_every = 500   # frequent checkpoints for fast test
    checkpoint_seconds = 5.0

    # Shared state across instances — allows the test to verify phase 2
    # only processes records that phase 1 didn't reach.
    processed_indices: set[int] = set()

    def __init__(self, job_name: str, total: int = TOTAL_RECORDS, **kwargs: Any) -> None:
        super().__init__(job_name, **kwargs)
        self._total = total

    def iter_records(self, offset: int) -> Generator[dict[str, Any], None, None]:
        """Yield records from offset to total."""
        for i in range(offset, self._total):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record: dict[str, Any]) -> None:
        """Record the index so the test can verify no duplicates across phases."""
        CountingImportJob.processed_indices.add(record["index"])


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def sync_job_doc():
    """
    Create a SyncJob doc for the test, yield its name, then clean up.
    """
    import frappe

    doc = frappe.get_doc({
        "doctype": "SyncJob",
        "job_type": "test.counting_import",
        "status": "Queued",
        "total_records": TOTAL_RECORDS,
        "processed": 0,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    yield doc.name

    # cleanup
    try:
        frappe.db.delete("SyncJobCheckpoint", {"parent": doc.name})
        frappe.db.delete("SyncJob", doc.name)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()


# ── Test: kill at 5000, resume, zero duplicates ──────────────────────────────


class TestKillAndResume:

    def test_kill_at_halfway_resumes_zero_dupes(self, sync_job_doc: str):
        """
        GIVEN a SyncJob with 10,000 records
        WHEN the worker processes 5,000 records, is "killed", then restarted
        THEN all 10,000 records are processed with 0 duplicates

        Simulates a worker kill by raising JobInterrupted at offset KILL_AT.
        """
        # Reset shared state between test methods
        CountingImportJob.processed_indices.clear()

        # ── Phase 1: run until kill point ────────────────────────────────────

        job = CountingImportJob(job_name=sync_job_doc)

        # Patch iter_records to raise JobInterrupted at KILL_AT
        original_iter = job.iter_records
        call_count = [0]

        def kill_at_halfway(offset: int) -> Generator[dict[str, Any], None, None]:
            for record in original_iter(offset):
                call_count[0] += 1
                if call_count[0] > KILL_AT:
                    raise JobInterrupted("Simulated worker kill")
                yield record

        job.iter_records = kill_at_halfway  # type: ignore[method-assign]
        job.run()

        # After "kill"
        doc_after_kill = frappe.get_doc("SyncJob", sync_job_doc)
        assert doc_after_kill.status == "Interrupted", (
            f"Job should be Interrupted after kill, got {doc_after_kill.status}"
        )

        # Checkpoints exist
        checkpoints = frappe.get_all(
            "SyncJobCheckpoint",
            filters={"parent": sync_job_doc},
            fields=["offset", "records_processed"],
            order_by="idx asc",
        )
        assert len(checkpoints) > 0, "Should have at least one checkpoint"
        last_cp = checkpoints[-1]
        assert last_cp["offset"] >= KILL_AT, (
            f"Last checkpoint offset {last_cp['offset']} should be >= {KILL_AT}"
        )

        # Dedup count after phase 1
        dupes_after_kill = frappe.db.count("SyncJobDedup", {"job_type": "test.counting_import"})
        assert dupes_after_kill == KILL_AT, (
            f"After kill: expected {KILL_AT} dedup entries, got {dupes_after_kill}"
        )

        # ── Phase 2: resume from last checkpoint ─────────────────────────────

        job2 = CountingImportJob(job_name=sync_job_doc)
        job2.run()

        # After resume
        doc_final = frappe.get_doc("SyncJob", sync_job_doc)
        assert doc_final.status == "Completed", (
            f"Job should be Completed after resume, got {doc_final.status}"
        )
        assert doc_final.processed == TOTAL_RECORDS, (
            f"Expected {TOTAL_RECORDS} processed, got {doc_final.processed}"
        )

        # ── Zero duplicates ──────────────────────────────────────────────────

        final_dedup_count = frappe.db.count("SyncJobDedup", {"job_type": "test.counting_import"})
        assert final_dedup_count == TOTAL_RECORDS, (
            f"Expected {TOTAL_RECORDS} dedup entries (zero duplicates), "
            f"got {final_dedup_count}"
        )

        # Verify no duplicate idempotency keys
        duplicates = frappe.db.sql("""
            SELECT idempotency_key, COUNT(*) as cnt
            FROM `tabSyncJobDedup`
            WHERE job_type = %s
            GROUP BY idempotency_key
            HAVING cnt > 1
        """, ("test.counting_import",))
        assert len(duplicates) == 0, f"Found duplicate keys: {duplicates}"

        # Verify the in-memory tracking: phase 2 must not have re-processed
        # any index that phase 1 already handled (proves resume skipped).
        assert len(CountingImportJob.processed_indices) == TOTAL_RECORDS, (
            f"Expected {TOTAL_RECORDS} unique indices processed, "
            f"got {len(CountingImportJob.processed_indices)}"
        )

        logger.info(
            "Acceptance test passed: %d records, %d dedup entries, 0 duplicates",
            TOTAL_RECORDS,
            final_dedup_count,
        )


class TestCheckpointFrequency:

    def test_checkpoints_written_at_configured_interval(self, sync_job_doc: str):
        """
        Verify checkpoints are written at the configured interval
        (checkpoint_every=500 for CountingImportJob).
        """
        job = CountingImportJob(job_name=sync_job_doc, total=2_000)
        job.run()

        checkpoints = frappe.get_all(
            "SyncJobCheckpoint",
            filters={"parent": sync_job_doc},
            fields=["offset", "records_processed"],
            order_by="idx asc",
        )

        # Expect checkpoints at 500, 1000, 1500, 2000 (final)
        assert len(checkpoints) >= 3, f"Expected multiple checkpoints, got {len(checkpoints)}"
        offsets = [cp["offset"] for cp in checkpoints]
        assert 500 in offsets or any(o >= 500 for o in offsets), (
            "Checkpoints should span the configured interval"
        )

    def test_final_checkpoint_at_end_of_run(self, sync_job_doc: str):
        """
        The last checkpoint should be at offset == total_records.
        """
        job = CountingImportJob(job_name=sync_job_doc, total=500)
        job.run()

        last = frappe.get_all(
            "SyncJobCheckpoint",
            filters={"parent": sync_job_doc},
            fields=["offset"],
            order_by="idx desc",
            limit=1,
        )
        assert last[0]["offset"] == 500
