"""
fde_component.tests.test_dlq
==============================
DLQ retry, dead-lettering, and re-drive tests.

Compatible with Frappe's unittest-based test runner.
"""

from __future__ import annotations

import json
import unittest

import frappe

from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobFailed, JobDeadLettered
from fde_component.jobs.idempotency import IdempotencyKey
from fde_component.doctypes.sync_job_dlq.sync_job_dlq import re_drive


TOTAL_RECORDS = 100


# ── Test job classes ─────────────────────────────────────────────────────────


class FailingJob(CheckpointedJob):
    job_type = "test.failing_job"
    max_retries = 2
    checkpoint_every = 50
    checkpoint_seconds = 5.0

    def __init__(self, job_name: str, total: int = TOTAL_RECORDS, **kwargs):
        super().__init__(job_name, **kwargs)
        self._total = total

    def iter_records(self, offset: int, redrive_key: str | None = None):
        for i in range(offset, self._total):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        raise JobFailed("Simulated failure")


class MixedJob(CheckpointedJob):
    job_type = "test.mixed_job"
    max_retries = 1
    checkpoint_every = 50

    def __init__(self, job_name: str, **kwargs):
        super().__init__(job_name, **kwargs)
        self._fail_at = {3, 7}

    def iter_records(self, offset: int, redrive_key: str | None = None):
        for i in range(offset, 10):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        if record["index"] in self._fail_at:
            raise JobFailed("Simulated failure")


class ScopedJob(CheckpointedJob):
    """
    Test job that honors redrive_key in iter_records().
    When redrive_key is set, only the matching record is yielded.
    """
    job_type = "test.redrive_scope"
    max_retries = 1
    checkpoint_every = 1000

    def __init__(self, job_name: str, **kwargs):
        super().__init__(job_name, **kwargs)
        self._fail_at = {2}

    def iter_records(self, offset: int, redrive_key: str | None = None):
        for i in range(offset, 5):
            if redrive_key is not None:
                key = IdempotencyKey(self.job_type, f"record-{i}", "").hex
                if key != redrive_key:
                    continue
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        if record["index"] in self._fail_at:
            raise JobFailed("Simulated failure")
        self._processed_ids.append(record["index"])


# ── Base test class with helpers ─────────────────────────────────────────────


class DLQTestCase(unittest.TestCase):
    """Base class providing setUp/tearDown for DLQ tests."""

    def setUp(self):
        """Ensure no leftover dedup or DLQ rows from prior tests."""
        frappe.db.delete("SyncJobDedup", {})
        frappe.db.delete("SyncJobDLQ", {})
        frappe.db.commit()

    def _make_sync_job_doc(self, job_type: str, total_records: int = TOTAL_RECORDS,
                           source_config: str = "{}") -> str:
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": job_type,
            "status": "Queued",
            "total_records": total_records,
            "processed": 0,
            "source_config": source_config,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def _cleanup_sync_job(self, doc_name: str, job_type: str = ""):
        try:
            frappe.db.delete("SyncJobDLQ", {"sync_job": doc_name})
            frappe.db.delete("SyncJobCheckpoint", {"parent": doc_name})
            if job_type:
                frappe.db.delete("SyncJobDedup", {"job_type": job_type})
            frappe.db.delete("SyncJob", doc_name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()


# ── Test classes ─────────────────────────────────────────────────────────────


class TestDLQ(DLQTestCase):

    def test_job_fails_after_max_retries(self):
        doc_name = self._make_sync_job_doc("test.failing_job")
        try:
            job = FailingJob(job_name=doc_name, total=TOTAL_RECORDS)
            job.run()

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Dead Lettered")
        finally:
            self._cleanup_sync_job(doc_name, job_type="test.failing_job")

    def test_dlq_entries_created(self):
        doc_name = self._make_sync_job_doc("test.failing_job")
        try:
            job = FailingJob(job_name=doc_name, total=TOTAL_RECORDS)
            job.run()

            dlq_count = frappe.db.count("SyncJobDLQ", {"sync_job": doc_name})
            self.assertGreater(dlq_count, 0)
        finally:
            self._cleanup_sync_job(doc_name, job_type="test.failing_job")

    def test_successful_records_not_affected_by_dlq(self):
        doc_name = self._make_sync_job_doc("test.mixed_job", total_records=10)
        try:
            doc = frappe.get_doc("SyncJob", doc_name)
            doc.total_records = 10
            doc.save(ignore_permissions=True)
            frappe.db.commit()

            job = MixedJob(job_name=doc_name)
            job.run()

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Dead Lettered")

            dlq_count = frappe.db.count("SyncJobDLQ", {"sync_job": doc_name})
            self.assertEqual(dlq_count, 2)

            dedup_count = frappe.db.count("SyncJobDedup", {"job_type": "test.mixed_job"})
            self.assertEqual(dedup_count, 10)
        finally:
            self._cleanup_sync_job(doc_name, job_type="test.mixed_job")


class TestRedriveScoping(DLQTestCase):
    """
    Regression test: redrive must scope the new job to the single failed
    record so the source only yields that record, not the full dataset.
    """

    def test_redrive_scopes_to_single_record(self):
        doc_name = self._make_sync_job_doc("test.redrive_scope", total_records=5)
        try:
            # Phase 1: run original job — index 2 fails
            ScopedJob._processed_ids = []
            job = ScopedJob(job_name=doc_name)
            job.run()

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Dead Lettered")

            # All 5 records should have dedup entries (including DLQ'd index 2)
            dedup_count = frappe.db.count("SyncJobDedup", {"job_type": "test.redrive_scope"})
            self.assertEqual(dedup_count, 5)

            # Verify index 2 is in DLQ
            dlq_entries = frappe.get_all(
                "SyncJobDLQ",
                filters={"sync_job": doc_name},
                fields=["idempotency_key"],
            )
            self.assertEqual(len(dlq_entries), 1)
            failed_key = dlq_entries[0]["idempotency_key"]

            # Reset tracking for redrive run
            ScopedJob._processed_ids = []

            # Phase 2: re_drive the failed record
            re_drive(dlq_entries[0])

            # Find the child job
            child_jobs = frappe.get_all(
                "SyncJob",
                filters={"parent_job": doc_name, "job_type": "test.redrive_scope"},
                fields=["name", "source_config", "status"],
            )
            self.assertEqual(len(child_jobs), 1)
            child_job_name = child_jobs[0]["name"]

            # Verify redrive_key in source_config
            child_config = json.loads(child_jobs[0]["source_config"] or "{}")
            self.assertEqual(child_config.get("redrive_key"), failed_key)

            # Phase 3: run the child job
            child_job = ScopedJob(job_name=child_job_name)
            child_job._fail_at = set()  # redriven record should succeed
            child_job.run()

            # Only the failed record (index 2) reaches process_record
            self.assertEqual(ScopedJob._processed_ids, [2],
                             f"Expected only index 2, got {ScopedJob._processed_ids}")

            # Child job completed
            child_doc = frappe.get_doc("SyncJob", child_job_name)
            self.assertEqual(child_doc.status, "Completed")

            # Dedup: 5 original + 1 redrive = 6
            dedup_after = frappe.db.count("SyncJobDedup", {"job_type": "test.redrive_scope"})
            self.assertEqual(dedup_after, 6)

            # Failed index now has a completed dedup entry
            redriven_key = IdempotencyKey("test.redrive_scope", "record-2", "").hex
            exists = frappe.db.exists(
                "SyncJobDedup",
                {"job_type": "test.redrive_scope",
                 "idempotency_key": redriven_key,
                 "status": "completed"},
            )
            self.assertTrue(exists)
        finally:
            self._cleanup_sync_job(doc_name, job_type="test.redrive_scope")
            # Cleanup child jobs
            for d in frappe.get_all("SyncJob", filters={"parent_job": doc_name}, pluck="name"):
                try:
                    frappe.delete_doc("SyncJob", d, ignore_permissions=True)
                except Exception:
                    pass
            frappe.db.commit()
