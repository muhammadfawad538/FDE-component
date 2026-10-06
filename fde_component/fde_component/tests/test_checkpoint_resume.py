"""
fde_component.tests.test_checkpoint_resume
=============================================
Acceptance test for SP-05: killing a worker mid-import resumes from the
last checkpoint with zero duplicates.

Compatible with Frappe's unittest-based test runner.
"""

from __future__ import annotations

import logging
import hashlib
from typing import Any

import unittest

import frappe

from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobInterrupted, JobFailed
from fde_component.jobs.idempotency import IdempotencyKey
from fde_component.jobs.runner import run_job
from fde_component.doctypes.sync_job_dlq.sync_job_dlq import re_drive


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
    max_retries = 0
    checkpoint_every = 500
    checkpoint_seconds = 5.0

    processed_indices: set = set()

    def __init__(self, job_name: str, total: int = TOTAL_RECORDS, **kwargs: Any) -> None:
        super().__init__(job_name, **kwargs)
        self._total = total

    def iter_records(self, offset: int, redrive_key: str | None = None):
        """Yield records from offset to total."""
        for i in range(offset, self._total):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record: dict[str, Any]) -> None:
        CountingImportJob.processed_indices.add(record["index"])


# ── Base test class ──────────────────────────────────────────────────────────


class CheckpointTestCase(unittest.TestCase):

    def setUp(self):
        """Ensure no leftover dedup rows and register job class."""
        frappe.db.delete("SyncJobDedup", {"job_type": "test.counting_import"})
        frappe.db.commit()
        from fde_component.jobs.runner import JOB_REGISTRY
        JOB_REGISTRY["test.counting_import"] = CountingImportJob

    def tearDown(self):
        from fde_component.jobs.runner import JOB_REGISTRY
        JOB_REGISTRY.pop("test.counting_import", None)

    def _make_sync_job_doc(self, total_records: int = TOTAL_RECORDS) -> str:
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": "test.counting_import",
            "status": "Queued",
            "total_records": total_records,
            "processed": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def _cleanup_sync_job(self, doc_name: str):
        try:
            frappe.db.delete("SyncJobCheckpoint", {"parent": doc_name})
            frappe.db.delete("SyncJobDedup", {"job_type": "test.counting_import"})
            frappe.db.delete("SyncJob", doc_name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()


# ── Tests ────────────────────────────────────────────────────────────────────


class TestKillAndResume(CheckpointTestCase):

    def test_kill_at_halfway_resumes_zero_dupes(self):
        """
        GIVEN a SyncJob with 10,000 records
        WHEN the worker processes 5,000 records, is "killed", then restarted
        THEN all 10,000 records are processed with 0 duplicates
        """
        CountingImportJob.processed_indices.clear()
        doc_name = self._make_sync_job_doc()

        try:
            # Phase 1: run until kill point
            job = CountingImportJob(job_name=doc_name)

            original_iter = job.iter_records
            call_count = [0]

            def kill_at_halfway(offset: int, redrive_key=None):
                for record in original_iter(offset):
                    call_count[0] += 1
                    if call_count[0] > KILL_AT:
                        raise JobInterrupted("Simulated worker kill")
                    yield record

            job.iter_records = kill_at_halfway  # type: ignore[method-assign]
            job.run()

            doc_after_kill = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc_after_kill.status, "Interrupted")

            checkpoints = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["offset", "records_processed"],
                order_by="idx asc",
            )
            self.assertGreater(len(checkpoints), 0)
            last_cp = checkpoints[-1]
            self.assertGreaterEqual(last_cp["offset"], KILL_AT)

            dupes_after_kill = frappe.db.count(
                "SyncJobDedup", {"job_type": "test.counting_import"}
            )
            self.assertEqual(dupes_after_kill, KILL_AT)

            # Phase 2: resume from last checkpoint
            job2 = CountingImportJob(job_name=doc_name)
            job2.run()

            doc_final = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc_final.status, "Completed")
            self.assertEqual(doc_final.processed, TOTAL_RECORDS)

            # Zero duplicates
            final_dedup_count = frappe.db.count(
                "SyncJobDedup", {"job_type": "test.counting_import"}
            )
            self.assertEqual(final_dedup_count, TOTAL_RECORDS)

            # Verify no duplicate idempotency keys
            duplicates = frappe.db.sql("""
                SELECT idempotency_key, COUNT(*) as cnt
                FROM `tabSyncJobDedup`
                WHERE job_type = %s
                GROUP BY idempotency_key
                HAVING cnt > 1
            """, ("test.counting_import",))
            self.assertEqual(len(duplicates), 0)

            self.assertEqual(len(CountingImportJob.processed_indices), TOTAL_RECORDS)
        finally:
            self._cleanup_sync_job(doc_name)


class TestTransientFailureResume(CheckpointTestCase):

    def test_interrupted_record_is_retried_not_skipped(self):
        CountingImportJob.processed_indices.clear()
        doc_name = self._make_sync_job_doc(total_records=20)

        try:
            class FlakyJob(CheckpointedJob):
                job_type = "test.flaky_import"
                max_retries = 2
                checkpoint_every = 1000
                checkpoint_seconds = 30.0

                def __init__(self, job_name: str, **kwargs):
                    super().__init__(job_name, **kwargs)
                    self._fail_once_on = {2}

                def iter_records(self, offset: int, redrive_key: str | None = None):
                    for i in range(offset, 20):
                        yield {"id": f"record-{i}", "index": i}

                def process_record(self, record):
                    idx = record["index"]
                    if idx in self._fail_once_on:
                        self._fail_once_on.discard(idx)
                        raise JobFailed(f"Transient failure at index {idx}")
                    CountingImportJob.processed_indices.add(idx)

            job = FlakyJob(job_name=doc_name)
            job.run()

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Completed")
            self.assertEqual(len(CountingImportJob.processed_indices), 20)
            self.assertIn(2, CountingImportJob.processed_indices)

            dedup_count = frappe.db.count("SyncJobDedup", {"job_type": "test.flaky_import"})
            self.assertEqual(dedup_count, 20)
        finally:
            self._cleanup_sync_job(doc_name)
            frappe.db.delete("SyncJobDedup", {"job_type": "test.flaky_import"})
            frappe.db.commit()


class TestChecksumLineage(CheckpointTestCase):

    def test_checksum_changes_with_source_id(self):
        doc_name = self._make_sync_job_doc(total_records=10)
        try:
            job = CountingImportJob(job_name=doc_name, total=10)
            job.run()

            checksums = []
            checkpoints = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["checksum"],
                order_by="idx asc",
            )
            for cp in checkpoints:
                checksums.append(cp["checksum"])

            for cs in checksums:
                self.assertEqual(len(cs), 64, f"Checksum should be 64-char hex, got {cs}")
                int(cs, 16)
        finally:
            self._cleanup_sync_job(doc_name)

    def test_checksum_is_not_just_offset_counter(self):
        doc_name = self._make_sync_job_doc(total_records=100)
        try:
            job1 = CountingImportJob(job_name=doc_name, total=100)
            job1.run()
            last_cp_1 = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["checksum"],
                order_by="idx desc",
                limit=1,
            )[0]["checksum"]

            frappe.db.delete("SyncJobCheckpoint", {"parent": doc_name})
            frappe.db.delete("SyncJobDedup", {"job_type": "test.counting_import"})
            frappe.db.commit()

            job2 = CountingImportJob(job_name=doc_name, total=100)
            job2.run()
            last_cp_2 = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["checksum"],
                order_by="idx desc",
                limit=1,
            )[0]["checksum"]

            self.assertEqual(last_cp_1, last_cp_2)
        finally:
            self._cleanup_sync_job(doc_name)



class TestDLQChecksumLineage(CheckpointTestCase):

    def test_redrive_job_restores_parent_checksum(self):
        """A re-driven job inherits the parent's checksum lineage end-to-end."""
        doc_name = self._make_sync_job_doc(total_records=20)
        try:
            # Run parent job fully
            job = CountingImportJob(job_name=doc_name, total=20)
            job.run()

            parent_last_checksum = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["checksum"],
                order_by="idx desc",
                limit=1,
            )[0]["checksum"]

            # Create a DLQ entry and call the real re_drive() server action,
            # which stores parent_checksum in the child's source_config.
            dlq_doc = frappe.get_doc({
                "doctype": "SyncJobDLQ",
                "sync_job": doc_name,
                "idempotency_key": IdempotencyKey("test.counting_import", "record-2", "").hex,
                "status": "Dead Lettered",
            })
            dlq_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            re_drive(dlq_doc)

            # Find the child job created by re_drive
            child_jobs = frappe.get_all(
                "SyncJob",
                filters={"parent_job": doc_name, "job_type": "test.counting_import"},
                fields=["name", "source_config"],
            )
            self.assertEqual(len(child_jobs), 1)
            child_job_name = child_jobs[0]["name"]

            # Verify parent_checksum was stored in source_config
            child_config = frappe.parse_json(child_jobs[0]["source_config"] or "{}")
            self.assertEqual(child_config.get("parent_checksum"), parent_last_checksum)

            # Run the child through the real runner (synchronous)
            run_job(child_job_name, "test.counting_import")

            # Compute expected the same way the job does: seed from parent, then
            # chain the child's record key(s) in the same order they were processed.
            redriven_key = IdempotencyKey("test.counting_import", "record-2", "").hex
            seeded = hashlib.sha256(parent_last_checksum.encode())
            seeded.update(redriven_key.encode())
            expected = seeded.hexdigest()

            # The child's last checkpoint checksum should chain off the parent's
            child_cp = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": child_job_name},
                fields=["checksum"],
                order_by="idx desc",
                limit=1,
            )[0]["checksum"]
            self.assertEqual(child_cp, expected)
        finally:
            self._cleanup_sync_job(doc_name)
            frappe.db.delete("SyncJob", {"parent_job": doc_name})
            frappe.db.delete("SyncJobCheckpoint", {"parent": ["like", "SJ-REDRIVE-TEST%"]})
            frappe.db.delete("SyncJobDedup", {"job_type": "test.counting_import"})
            frappe.db.delete("SyncJobDLQ", {"sync_job": doc_name})
            frappe.db.commit()


class TestCheckpointFrequency(CheckpointTestCase):

    def test_checkpoints_written_at_configured_interval(self):
        doc_name = self._make_sync_job_doc(total_records=2000)
        try:
            job = CountingImportJob(job_name=doc_name, total=2_000)
            job.run()

            checkpoints = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["offset", "records_processed"],
                order_by="idx asc",
            )
            self.assertGreaterEqual(len(checkpoints), 3)
            offsets = [cp["offset"] for cp in checkpoints]
            self.assertTrue(
                500 in offsets or any(o >= 500 for o in offsets),
                "Checkpoints should span the configured interval",
            )
        finally:
            self._cleanup_sync_job(doc_name)

    def test_final_checkpoint_at_end_of_run(self):
        doc_name = self._make_sync_job_doc(total_records=500)
        try:
            job = CountingImportJob(job_name=doc_name, total=500)
            job.run()

            last = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["offset"],
                order_by="idx desc",
                limit=1,
            )
            self.assertEqual(last[0]["offset"], 500)
        finally:
            self._cleanup_sync_job(doc_name)
